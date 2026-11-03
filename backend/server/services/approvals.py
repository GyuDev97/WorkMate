from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable
from uuid import uuid4

from server.models.schemas import ActionCard, ActionExecutionResult, ApprovalStatus
from server.services.activity import ActivityService


@dataclass
class PendingAction:
    card: ActionCard
    label: str
    stale: Callable[[], str | None]  # returns a reason when the target changed since the proposal
    run: Callable[[], str]


class ApprovalStore:
    """Shared approval lifecycle: a proposal never executes; approval runs once; rejection and failures are final."""

    unchanged_text = "데이터는 변경되지 않았습니다."

    def __init__(self, activity: ActivityService | None = None) -> None:
        self.activity = activity
        self._actions: dict[str, PendingAction] = {}
        self._lock = Lock()

    def _save(self, kind: str, heading: str, fields: dict[str, str], stale: Callable[[], str | None], run: Callable[[], str], label: str) -> ActionCard:
        action_id = str(uuid4())
        card = ActionCard(action_id=action_id, kind=kind, title=heading, fields=fields)  # type: ignore[arg-type]
        with self._lock:
            self._actions[action_id] = PendingAction(card, label, stale, run)
        return card

    def has(self, action_id: str) -> bool:
        return action_id in self._actions

    def _describe_error(self, error: Exception) -> str:
        return str(error)

    def _failure_status(self, error: Exception) -> ApprovalStatus:
        return ApprovalStatus.FAILED

    def approve(self, action_id: str) -> ActionExecutionResult:
        with self._lock:
            pending = self._actions.get(action_id)
            if not pending:
                raise KeyError("Action not found")
            card = pending.card
            if card.status == ApprovalStatus.COMPLETED:
                return ActionExecutionResult(action=card, message="이 작업은 이미 완료되었습니다.")
            if card.status == ApprovalStatus.EXECUTING:
                return ActionExecutionResult(action=card, message="작업을 실행 중입니다.")
            if card.status != ApprovalStatus.PENDING_APPROVAL:
                return ActionExecutionResult(action=card, message="이미 처리되어 실행할 수 없는 작업입니다.")
            card.status = ApprovalStatus.EXECUTING
        try:
            reason = pending.stale()
            if reason:
                card.status = ApprovalStatus.FAILED
                self._record(f"{pending.label} 실패", "승인 시점에 대상이 변경되어 실행하지 않았습니다.")
                return ActionExecutionResult(action=card, message=f"실행하지 않았습니다. {reason}")
            message = pending.run()
            card.status = ApprovalStatus.COMPLETED
            self._record(f"{pending.label} 승인", "사용자 승인 후 실행했습니다.")
        except Exception as error:
            card.status = self._failure_status(error)
            unknown = card.status == ApprovalStatus.NEEDS_CHECK
            message = f"확인이 필요합니다: {self._describe_error(error)} 자동으로 다시 실행하지 않습니다." if unknown else f"실행하지 못했습니다: {self._describe_error(error)}"
            self._record(f"{pending.label} {'확인 필요' if unknown else '실패'}", type(error).__name__)
        return ActionExecutionResult(action=card, message=message)

    def reject(self, action_id: str) -> ActionExecutionResult:
        with self._lock:
            pending = self._actions.get(action_id)
            if not pending:
                raise KeyError("Action not found")
            card = pending.card
            if card.status != ApprovalStatus.PENDING_APPROVAL:
                return ActionExecutionResult(action=card, message="이미 처리된 작업입니다.")
            card.status = ApprovalStatus.REJECTED
        self._record(f"{pending.label} 거절", f"사용자가 거절했습니다. {self.unchanged_text}", "system")
        return ActionExecutionResult(action=card, message=f"요청을 취소했습니다. {self.unchanged_text}")

    def _record(self, action: str, detail: str, category: str = "create") -> None:
        if self.activity:
            self.activity.record(action, detail, category)

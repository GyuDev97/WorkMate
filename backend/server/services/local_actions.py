from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from server.models.schemas import check_period, ActionCard, Memo, MemoCreate, MemoUpdate, Priority, Task, TaskCreate, TaskStatus, TaskUpdate
from server.services.activity import ActivityService
from server.services.approvals import ApprovalStore
from server.services.dates import SEOUL, parse_due, parse_start
from server.services.memos import MemoService
from server.services.tasks import TaskService

PRIORITY_LABEL = {Priority.HIGH: "높음", Priority.MEDIUM: "보통", Priority.LOW: "낮음"}
STATUS_LABEL = {TaskStatus.TODO: "할 일", TaskStatus.IN_PROGRESS: "진행 중", TaskStatus.DONE: "완료", TaskStatus.CANCELLED: "취소"}
CONTENT_PREVIEW = 600
Proposal = ActionCard | dict[str, Any]


def _when(value: datetime | None, empty: str = "기한 없음") -> str:
    return value.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M") if value else empty


def _preview(text: str) -> str:
    return text if len(text) <= CONTENT_PREVIEW else f"{text[:CONTENT_PREVIEW]}… (총 {len(text)}자 중 일부)"


class LocalActionService(ApprovalStore):
    """Approval-gated Task/Memo mutations. Proposals never touch the DB; execution reuses TaskService/MemoService."""

    def __init__(self, tasks: TaskService, memos: MemoService, activity: ActivityService | None = None) -> None:
        super().__init__(activity)
        self.tasks, self.memos = tasks, memos

    # ----- target resolution (never silently picks among several matches) -----
    def resolve_task(self, target: str) -> tuple[Task | None, dict[str, Any] | None]:
        target = target.strip()
        if not target:
            return None, {"error": "대상 업무를 지정해 주세요."}
        by_id = self.tasks.get_task(target)
        if by_id:
            return by_id, None
        needle = target.casefold()
        everything = self.tasks.list_tasks()
        pool = [t for t in everything if t.title.casefold() == needle] or [t for t in everything if needle in t.title.casefold()]
        if len(pool) == 1:
            return pool[0], None
        if not pool:
            return None, {"error": f"‘{target}’에 해당하는 업무를 찾지 못했습니다."}
        return None, self._ambiguous("업무", target, [{"id": t.id, "title": t.title, "status": t.status.value, "due_at": _when(t.due_at)} for t in pool[:10]])

    def resolve_memo(self, target: str) -> tuple[Memo | None, dict[str, Any] | None]:
        target = target.strip()
        if not target:
            return None, {"error": "대상 메모를 지정해 주세요."}
        by_id = self.memos.get_memo(target)
        if by_id:
            return by_id, None
        needle = target.casefold()
        everything = self.memos.list_memos()
        pool = [m for m in everything if m.title.casefold() == needle] or [m for m in everything if needle in m.title.casefold()]
        if len(pool) == 1:
            return pool[0], None
        if not pool:
            return None, {"error": f"‘{target}’에 해당하는 메모를 찾지 못했습니다."}
        return None, self._ambiguous("메모", target, [{"id": m.id, "title": m.title, "updated_at": _when(m.updated_at)} for m in pool[:10]])

    @staticmethod
    def _ambiguous(noun: str, target: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {"status": "needs_clarification", "message": f"‘{target}’와 일치하는 {noun}이 {len(candidates)}개 이상입니다. 어느 것인지 사용자에게 확인하세요. 임의로 고르지 마세요.", "candidates": candidates}

    # ----- task proposals -----
    def propose_task_create(self, title: str, description: str, priority: Priority, due: str | None, start: str | None = None) -> Proposal:
        due_at = parse_due(due) if due else None
        start_at = parse_start(start) if start else None
        try:
            payload = TaskCreate(title=title, description=description, priority=priority, start_at=start_at, due_at=due_at, source="agent")
        except ValueError:
            return {"error": "시작일이 종료일보다 늦어 업무를 만들 수 없습니다. 날짜를 다시 확인해 달라고 안내하세요."}
        fields = {"제목": payload.title, "우선순위": PRIORITY_LABEL[priority], "시작일": _when(start_at, "시작일 없음"), "종료일(마감)": _when(due_at)}
        if description:
            fields["내용"] = _preview(description)
        return self._save("task_create", "업무 추가", fields, lambda: None, lambda: f"‘{self.tasks.create_task(payload).title}’ 업무를 추가했습니다.", "업무 추가")

    def propose_task_update(self, target: str, title: str | None, description: str | None, priority: Priority | None, due: str | None, clear_due: bool, status: TaskStatus | None, start: str | None = None, clear_start: bool = False) -> Proposal:
        task, problem = self.resolve_task(target)
        if problem:
            return problem
        assert task is not None
        changes: dict[str, Any] = {}
        lines: list[str] = []
        if title is not None and title != task.title:
            changes["title"] = title; lines.append(f"제목: {task.title} → {title}")
        if description is not None and description != task.description:
            changes["description"] = description; lines.append(f"내용: {_preview(task.description) or '(비어 있음)'} → {_preview(description) or '(비어 있음)'}")
        if priority is not None and priority != task.priority:
            changes["priority"] = priority; lines.append(f"우선순위: {PRIORITY_LABEL[task.priority]} → {PRIORITY_LABEL[priority]}")
        if clear_due and task.due_at is not None:
            changes["due_at"] = None; lines.append(f"종료일(마감): {_when(task.due_at)} → 기한 없음")
        elif due and not clear_due:
            new_due = parse_due(due)
            if task.due_at is None or new_due != task.due_at.astimezone(SEOUL):
                changes["due_at"] = new_due; lines.append(f"종료일(마감): {_when(task.due_at)} → {_when(new_due)}")
        if clear_start and task.start_at is not None:
            changes["start_at"] = None; lines.append(f"시작일: {_when(task.start_at, '시작일 없음')} → 시작일 없음")
        elif start and not clear_start:
            new_start = parse_start(start)
            if task.start_at is None or new_start != task.start_at.astimezone(SEOUL):
                changes["start_at"] = new_start; lines.append(f"시작일: {_when(task.start_at, '시작일 없음')} → {_when(new_start)}")
        if status is not None and status != task.status:
            changes["status"] = status; lines.append(f"상태: {STATUS_LABEL[task.status]} → {STATUS_LABEL[status]}")
        if not changes:
            return {"status": "no_change", "message": f"‘{task.title}’ 업무에 적용할 변경 사항이 없습니다."}
        TaskUpdate(**changes)  # validate before offering the card
        try:  # the resulting period (changed values merged with the stored ones) must stay consistent
            check_period(changes["start_at"] if "start_at" in changes else task.start_at, changes["due_at"] if "due_at" in changes else task.due_at)
        except ValueError:
            return {"error": "변경하면 시작일이 종료일보다 늦어집니다. 날짜를 다시 확인해 달라고 안내하세요."}
        only_status = set(changes) == {"status"}
        kind, heading = ("task_complete", "업무 완료 처리") if only_status and changes["status"] == TaskStatus.DONE else ("task_update", "업무 수정")
        snapshot, task_id = task.updated_at, task.id
        return self._save(kind, heading, {"대상": task.title, "변경": "\n".join(lines)}, lambda: self._task_stale(task_id, snapshot),
                          lambda: f"‘{(self.tasks.update_task(task_id, TaskUpdate(**changes)) or task).title}’ 업무를 {'완료 처리' if kind == 'task_complete' else '수정'}했습니다.", heading)

    def propose_task_delete(self, target: str) -> Proposal:
        task, problem = self.resolve_task(target)
        if problem:
            return problem
        assert task is not None
        snapshot, task_id = task.updated_at, task.id
        fields = {"대상": task.title, "상태": STATUS_LABEL[task.status], "시작일": _when(task.start_at, "시작일 없음"), "종료일(마감)": _when(task.due_at), "주의": "삭제하면 복구할 수 없습니다. 연결된 메모는 유지됩니다."}
        return self._save("task_delete", "업무 삭제", fields, lambda: self._task_stale(task_id, snapshot),
                          lambda: (self.tasks.delete_task(task_id) and f"‘{task.title}’ 업무를 삭제했습니다.") or "업무가 이미 삭제되어 있습니다.", "업무 삭제")

    def _task_stale(self, task_id: str, snapshot: datetime) -> str | None:
        current = self.tasks.get_task(task_id)
        if current is None:
            return "대상 업무가 이미 삭제되었습니다."
        return "승인 전에 이 업무가 변경되었습니다. 최신 내용으로 다시 요청해 주세요." if current.updated_at != snapshot else None

    # ----- memo proposals -----
    def propose_memo_create(self, title: str, content: str, task_target: str | None) -> Proposal:
        task_id, link_name = None, None
        if task_target:
            task, problem = self.resolve_task(task_target)
            if problem:
                return problem
            assert task is not None
            task_id, link_name = task.id, task.title
        payload = MemoCreate(title=title, content=content, task_id=task_id)
        fields = {"제목": payload.title, "내용": _preview(content) or "(비어 있음)"}
        if link_name:
            fields["연결 업무"] = link_name
        stale = (lambda: None if self.tasks.get_task(task_id) else "연결하려는 업무가 삭제되었습니다.") if task_id else (lambda: None)
        return self._save("memo_create", "메모 작성", fields, stale, lambda: f"‘{self.memos.create_memo(payload).title}’ 메모를 저장했습니다.", "메모 작성")

    def propose_memo_update(self, target: str, title: str | None, content: str | None, task_target: str | None, unlink_task: bool) -> Proposal:
        memo, problem = self.resolve_memo(target)
        if problem:
            return problem
        assert memo is not None
        changes: dict[str, Any] = {}
        lines: list[str] = []
        if title is not None and title != memo.title:
            changes["title"] = title; lines.append(f"제목: {memo.title} → {title}")
        if content is not None and content != memo.content:
            changes["content"] = content; lines.append(f"내용: {_preview(memo.content) or '(비어 있음)'} → {_preview(content) or '(비어 있음)'}")
        if unlink_task and memo.task_id:
            changes["task_id"] = None; lines.append(f"연결 업무: {memo.task_title or memo.task_id} → 연결 해제")
        elif task_target and not unlink_task:
            task, task_problem = self.resolve_task(task_target)
            if task_problem:
                return task_problem
            assert task is not None
            if task.id != memo.task_id:
                changes["task_id"] = task.id; lines.append(f"연결 업무: {memo.task_title or '없음'} → {task.title}")
        if not changes:
            return {"status": "no_change", "message": f"‘{memo.title}’ 메모에 적용할 변경 사항이 없습니다."}
        MemoUpdate(**changes)
        memo_id, snapshot = memo.id, memo.updated_at

        def stale() -> str | None:
            current = self.memos.get_memo(memo_id)
            if current is None:
                return "대상 메모가 이미 삭제되었습니다."
            return "승인 전에 이 메모가 변경되었습니다. 최신 내용으로 다시 요청해 주세요." if current.updated_at != snapshot else None

        return self._save("memo_update", "메모 수정", {"대상": memo.title, "변경": "\n".join(lines)}, stale,
                          lambda: f"‘{(self.memos.update_memo(memo_id, MemoUpdate(**changes)) or memo).title}’ 메모를 수정했습니다.", "메모 수정")

    def propose_memo_delete(self, target: str) -> Proposal:
        memo, problem = self.resolve_memo(target)
        if problem:
            return problem
        assert memo is not None
        memo_id, snapshot = memo.id, memo.updated_at

        def stale() -> str | None:
            current = self.memos.get_memo(memo_id)
            if current is None:
                return "대상 메모가 이미 삭제되었습니다."
            return "승인 전에 이 메모가 변경되었습니다. 최신 내용으로 다시 요청해 주세요." if current.updated_at != snapshot else None

        fields = {"대상": memo.title, "내용": _preview(memo.content) or "(비어 있음)", "주의": "삭제하면 복구할 수 없습니다."}
        return self._save("memo_delete", "메모 삭제", fields, stale,
                          lambda: (self.memos.delete_memo(memo_id) and f"‘{memo.title}’ 메모를 삭제했습니다.") or "메모가 이미 삭제되어 있습니다.", "메모 삭제")

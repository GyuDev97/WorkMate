from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from server.database.repository import ConversationRepository
from server.models.schemas import ActionCard, ActionExecutionResult, ApprovalStatus, ChatResponse, ConversationDetail, ConversationSummary, StoredMessage, ToolTrace

CONTEXT_MESSAGES = 8  # the model only ever sees the most recent messages, however long the stored conversation is
LIVE_STATES = {ApprovalStatus.PENDING_APPROVAL, ApprovalStatus.EXECUTING}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # OpenAI-style API keys
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),       # Google OAuth access tokens
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),          # Google OAuth refresh tokens
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),         # Google API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),      # Google OAuth client secrets
]


def redact(text: str) -> str:
    """Credentials never go into the conversation database, even if someone pastes one into the chat."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[삭제됨]", text)
    return text


def storable(action: ActionCard | None) -> ActionCard | None:
    """Mail cards show recipients, subjects and body previews on screen only; the stored copy keeps just the kind, title and status."""
    if action is not None and action.kind.startswith("mail_"):
        return action.model_copy(update={"fields": {"안내": "메일 내용은 보안상 저장되지 않습니다. 메일 화면에서 확인하세요."}})
    return action


class ConversationService:
    """Stores Assistant conversations and restores them. Restoring only reads; it never re-runs a tool or an action."""

    def __init__(self, repository: ConversationRepository, agent: Callable[[], Any], approval_stores: Callable[[], tuple[Any, ...]]) -> None:
        self.repository, self._agent, self._approval_stores = repository, agent, approval_stores

    # ----- chat -----
    def chat(self, message: str, conversation_id: str | None = None) -> ChatResponse:
        cid = self.repository.ensure(conversation_id or str(uuid4()))
        context = self.repository.context(cid, CONTEXT_MESSAGES)  # loaded before the new message is stored
        response = self._agent().respond(message, cid, history=context)
        self.repository.add_message(cid, "user", redact(message))
        self.repository.add_message(cid, "assistant", redact(response.message), trace=response.trace, sources=response.sources, action=storable(response.action))
        response.conversation_id = cid
        return response

    # ----- read -----
    def list_conversations(self) -> list[ConversationSummary]:
        return self.repository.list()

    def get(self, conversation_id: str) -> ConversationDetail | None:
        found = self.repository.get(conversation_id)
        if not found:
            return None
        summary, rows = found
        return ConversationDetail(**summary.model_dump(), messages=[self._message(row) for row in rows])

    def delete(self, conversation_id: str) -> bool:
        return self.repository.delete(conversation_id)

    # ----- approvals -----
    def record_decision(self, result: ActionExecutionResult) -> None:
        """Keep the stored card in step with what actually happened, and log the outcome once per transition."""
        row = self.repository.find_by_action(result.action.action_id or "")
        if not row:
            return
        stored = ActionCard.model_validate_json(row["action"])
        if stored.status == result.action.status:
            return
        self.repository.update_action(row["id"], storable(result.action))
        self.repository.add_message(row["conversation_id"], "assistant", redact(result.message))

    # ----- helpers -----
    def _message(self, row: Any) -> StoredMessage:
        action = ActionCard.model_validate_json(row["action"]) if row["action"] else None
        if action and action.action_id and action.status in LIVE_STATES and not any(store.has(action.action_id) for store in self._approval_stores()):
            action = action.model_copy(update={"status": ApprovalStatus.EXPIRED})  # the backend restarted: this card can no longer run
        return StoredMessage(
            id=row["id"], seq=row["seq"], role=row["role"], content=row["content"], action=action,
            trace=[ToolTrace(**item) for item in json.loads(row["trace"])] if row["trace"] else [],
            sources=json.loads(row["sources"]) if row["sources"] else [], created_at=row["created_at"],
        )

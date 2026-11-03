from __future__ import annotations

from collections import defaultdict, deque
import logging
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from server.agent.contracts import ToolCallingProvider
from server.agent.tool_registry import ToolRegistry
from server.models.schemas import ActionCard, ChatResponse, ToolTrace
from server.services.activity import ActivityService

SYSTEM_INSTRUCTIONS = """You are workMate, a Korean personal work assistant. Answer greetings and general questions directly when no tool is needed. Use tools for facts about calendar, tasks, memos and WorkMemory. Mail content is never available to you: mail tools return only counts and status, and the user sees the actual list on screen. Never guess or invent mail subjects, senders, contents or attachments; point the user to the on-screen list or the Mail screen. If a mail tool reports not_connected or session_expired, say so plainly. To change existing mail (reply, forward, delete, move, mark read) tell the user to use the Mail screen. Ground structured claims only in tool outputs. If the user asks for real-time or external data and no matching tool exists (for example, weather), clearly say it is not connected; never invent it. Never claim a tool succeeded if it failed. Never send mail or change an external calendar: produce approval-required proposals instead. To create an event, require a clear date and time; if either is ambiguous, ask a concise follow-up. For update or delete, search first and do not choose among multiple matching events yourself. Task and memo rules: reading (list_tasks, list_memos, get_memo) needs no approval. Any create/update/complete/delete of a task or memo must go through a propose_* tool, which only prepares an Action Card; nothing is saved until the user approves it, so never say it was already done - say it is waiting for approval. Pass the task or memo as the user named it (or its id from a prior tool result). If a tool returns needs_clarification, list the candidates and ask which one; never choose yourself and never retry with a guessed id. Propose deletion only when the user explicitly asks to delete. Compute relative dates (오늘, 내일, 금요일 ...) from the current datetime below in Asia/Seoul and pass an explicit date. When saving a memo from the conversation, use only what was actually said in this conversation or by the user; if there is nothing to save, ask. Do not invent titles, dates or content the user did not give. Calendar rules: use list_events/get_event to read the calendar (dates are Asia/Seoul; compute 이번 주/내일/금요일 from the current datetime). A user merely mentioning a meeting or schedule is NOT a request to register it: call propose_calendar_event only when they clearly ask to add/register/create an event, and propose_calendar_update/delete only when they clearly ask to change/move/rename or delete/cancel one. For update/delete call propose_calendar_update/delete DIRECTLY with the title and date exactly as the user said them: the app searches the real calendar and asks which one when several match, so do not pre-search with list_events and never put the user's title words into list_events.query. Recurring events (is_recurring true, or the tool's unsupported notice) cannot be changed or deleted in this version: say so, and never offer to change or delete the whole series. If a tool returns needs_clarification, list the candidates and ask. When the user's request to add/change/delete something is clear and has all required details, you MUST call the matching propose_* tool in that same turn - never answer with text that merely describes the proposal or asks 'shall I register it?'. The Action Card is the confirmation step: never ask for confirmation in chat, and tell the user to review the card and press its button. Required details: a task needs only a title (priority, due date and description are optional - never ask for them); a memo needs a title and may have empty content; an event needs a title, date and time (or all-day). Ask a question only when a required detail or the target item is genuinely missing or ambiguous. One change per request unless the user asks for several; if a tool returns an error, report the failure honestly.

Conversation style: you are the user's personal secretary (개인 비서). Speak Korean in a warm, polite, concise 해요체 (예: "네, 확인해 볼게요.", "준비해 뒀어요. 카드를 확인하고 승인해 주세요."). Lead with the answer or the result, then add at most one short next-step suggestion when it is genuinely useful. Report like a capable secretary: "오늘 일정은 2건이에요" rather than long explanations. Acknowledge briefly ("알겠습니다", "네") before acting; do not over-apologize, flatter, or use emoji. Never fake familiarity or invent facts, dates or personal details to sound personable. Style changes only the tone: every rule above about tools, approvals and not inventing data still applies unchanged."""
logger = logging.getLogger("workmate.agent")


class WorkMateAgent:
    """Bounded application-side tool loop; providers decide calls, application enforces tools."""

    def __init__(self, provider: ToolCallingProvider, tools: ToolRegistry, activity: ActivityService | None = None, max_tool_rounds: int = 3) -> None:
        self.provider, self.tools, self.max_tool_rounds = provider, tools, max_tool_rounds
        self.activity = activity
        self._history: dict[str, deque[dict[str, str]]] = defaultdict(lambda: deque(maxlen=8))

    def respond(self, message: str, conversation_id: str = "default", history: list[dict[str, str]] | None = None) -> ChatResponse:
        """`history` is the stored context supplied by ConversationService; without it the agent falls back to its in-memory window."""
        persisted = history is not None
        history = list(history) if persisted else self._history[conversation_id]
        items: list[dict[str, Any]] = [{"role": item["role"], "content": item["content"]} for item in history]
        items.append({"role": "user", "content": message})
        traces: list[ToolTrace] = []
        action: ActionCard | None = None
        failures: list[str] = []
        ui_results: list[dict[str, Any]] = []
        final_text = ""
        logger.info("agent_request provider=%s conversation=%s chars=%d history=%d", self.provider.mode, conversation_id, len(message), len(history))
        for _round in range(self.max_tool_rounds):
            try:
                now = datetime.now(ZoneInfo("Asia/Seoul"))
                context = f"\n\nCurrent datetime: {now:%Y-%m-%d %H:%M} ({'월화수목금토일'[now.weekday()]}요일)\nTimezone: Asia/Seoul"
                turn = self.provider.respond(instructions=SYSTEM_INSTRUCTIONS + context, input_items=items, tools=self.tools.schemas())
            except Exception as error:
                logger.warning("agent_provider_error provider=%s type=%s", self.provider.mode, type(error).__name__)
                final_text = "LLM 연결 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                break
            items.extend(turn.provider_items)
            if not turn.tool_calls:
                final_text = turn.text or "요청을 처리했지만 응답을 생성하지 못했습니다."
                logger.info("agent_response provider=%s round=%d text_chars=%d", self.provider.mode, _round + 1, len(final_text))
                break
            logger.info("agent_tools_selected provider=%s round=%d tools=%s", self.provider.mode, _round + 1, ",".join(call.name for call in turn.tool_calls))
            for call in turn.tool_calls:
                result, trace = self.tools.execute(call.name, call.arguments)
                traces.append(trace)
                if self.activity: self.activity.record_tool_trace(trace)
                if not trace.success: failures.append(call.name)
                if ui := self.tools.ui_from(result): ui_results.append(ui)
                possible_action = self.tools.action_from(result)
                if possible_action: action = possible_action
                items.append({"type": "function_call_output", "call_id": call.call_id, "name": call.name, "output": self.tools.serialize(result)})
                logger.info("agent_tool_result tool=%s risk=%s success=%s duration_ms=%d", trace.tool, trace.risk.value, trace.success, trace.duration_ms)
        else:
            final_text = "요청 처리 중 도구 호출 한도에 도달했습니다. 필요한 정보를 좁혀 다시 요청해 주세요."
        if failures and final_text and "오류" not in final_text:
            final_text += "\n\n일부 정보 조회에 실패해, 확인된 결과만 안내했습니다."
        if not persisted:
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": final_text})
        sources = sorted({trace.tool for trace in traces if trace.success})
        logger.info("agent_complete provider=%s tools=%d action=%s", self.provider.mode, len(traces), action.kind if action else "none")
        return ChatResponse(message=final_text, action=action, sources=sources, trace=traces, ui_results=ui_results, metadata={"provider": self.provider.mode})

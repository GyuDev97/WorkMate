from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import count
from typing import Any
from zoneinfo import ZoneInfo

from server.agent.contracts import ProviderTurn, ToolCall


SEOUL = ZoneInfo("Asia/Seoul")
QUOTES = "‘’'\"“”` "
DUE_WORDS = r"(?:오늘|내일|모레|(?:이번 주 |다음 주 )?[월화수목금토일]요일)"
DAY = rf"(?:{DUE_WORDS}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}월\s*\d{{1,2}}일)"
STATUS_LABEL = {"TODO": "할 일", "IN_PROGRESS": "진행 중", "DONE": "완료", "CANCELLED": "취소"}
PRIORITY_LABEL = {"HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음"}


def _clean(value: str) -> str:
    return value.strip(QUOTES)


def _day(value: str, anchor: str | None = None) -> str:
    """'11월 5일' -> ISO date in the current year; other forms are passed through for the app to interpret."""
    if match := re.fullmatch(r"(\d{1,2})월\s*(\d{1,2})일", value.strip()):
        return f"{datetime.now(SEOUL).year}-{int(match[1]):02d}-{int(match[2]):02d}"
    return value.strip()


def _end_day(value: str, start: str) -> str:
    """'10일' after '11월 5일부터' means the same month as the start date."""
    if match := re.fullmatch(r"(\d{1,2})일", value.strip()):
        return f"{start[:8]}{int(match[1]):02d}"
    return _day(value)


def _kst(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone(SEOUL).strftime("%m-%d %H:%M") if value else "기한 없음"


class MockLLMProvider:
    """Deterministic tool-calling provider for offline development and test coverage."""
    name = "Mock LLM"
    mode = "mock"

    def __init__(self) -> None:
        self._ids = count(1)

    def respond(self, *, instructions: str, input_items: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ProviderTurn:
        tool_outputs = [item for item in input_items if item.get("type") == "function_call_output"]
        if tool_outputs:
            return ProviderTurn(text=self._summarize(tool_outputs), provider_items=[{"role": "assistant", "content": "Tool results summarized."}])
        user_text = next((str(item.get("content", "")) for item in reversed(input_items) if item.get("role") == "user"), "")
        calls = self._choose_tools(user_text)
        if not calls:
            return ProviderTurn(text=self._direct_response(user_text), provider_items=[{"role": "assistant", "content": "Direct response."}])
        return ProviderTurn(tool_calls=calls, provider_items=[{"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)} for call in calls])

    def _call(self, name: str, **arguments: Any) -> ToolCall:
        return ToolCall(call_id=f"mock-call-{next(self._ids)}", name=name, arguments=arguments)

    def _choose_task_memo_tools(self, text: str) -> list[ToolCall]:
        """Narrow keyword/regex rules only. This is NOT language understanding; free-form requests fall through."""
        if match := re.search(r"(.+?)\s*업무를?\s*삭제", text):
            return [self._call("propose_task_delete", target=_clean(match[1]))]
        if match := re.search(r"(.+?)\s*업무를?\s*완료", text):
            return [self._call("propose_task_update", target=_clean(match[1]), status="DONE")]
        if match := re.search(r"(.+?)\s*일정을?\s*삭제", text):
            return [self._call("propose_calendar_delete", target=_clean(match[1]))]
        if "일정" in text and any(token in text for token in ("보여", "알려", "찾")):
            if match := re.search(r"(이번 주|다음 주|\d{4}-\d{2}-\d{2})", text):
                return [self._call("list_events", start_date=match[1])]
        if match := re.search(rf"(.+?)\s*업무의\s*마감(?:일)?[을를]?\s*({DUE_WORDS})(?:으로|로)\s*(?:변경|수정)", text):
            return [self._call("propose_task_update", target=_clean(match[1]), due=match[2])]
        if match := re.search(rf"(.+?)\s*업무의\s*시작일(?:을|를)?\s*(?:없애|없앤|삭제|지워|해제)", text):
            return [self._call("propose_task_update", target=_clean(match[1]), clear_start=True)]
        if match := re.search(rf"(.+?)\s*업무의\s*시작일(?:을|를)?\s*({DAY})(?:으로|로)\s*(?:변경|수정)", text):
            return [self._call("propose_task_update", target=_clean(match[1]), start=_day(match[2]))]
        if match := re.search(rf"^(?P<start>{DAY})부터\s*(?:(?P<end>{DAY}|\d{{1,2}}일)까지\s*)?(?P<title>.+?)\s*업무(?:를|로)?\s*(?:추가|등록)", text):
            start = _day(match["start"])
            return [self._call("propose_task_create", title=_clean(match["title"]), start=start, due=_end_day(match["end"], start) if match["end"] else None)]
        if match := re.search(rf"^(?P<end>{DAY})까지\s*(?:완료할\s*)?(?P<title>.+?)\s*업무(?:를|로)?\s*(?:추가|등록)", text):
            return [self._call("propose_task_create", title=_clean(match["title"]), due=_day(match["end"]))]
        if match := re.search(rf"^(?:(?P<due>{DUE_WORDS})\s*마감(?:으로)?\s*)?(?P<title>.+?)\s*업무(?:를|로)?\s*(?:추가|등록)", text):
            return [self._call("propose_task_create", title=_clean(match["title"]), due=match["due"])]
        if "메모" in text and "삭제" in text and (match := re.search(r"(.+?)\s*메모를?\s*삭제", text)):
            return [self._call("propose_memo_delete", target=_clean(match[1]))]
        if "메모" in text and any(token in text for token in ("보여", "알려", "찾", "최근")) and "업무" not in text:
            query = ""
            if "최근" not in text and (match := re.search(r"(\S+)\s*(?:관련\s*)?메모", text)):
                query = _clean(match[1])
            return [self._call("list_memos", query=query)]
        if "업무" in text and any(token in text for token in ("보여", "알려", "찾")):
            if "마감" in text and "오늘" in text: return [self._call("list_tasks", view="due_today")]
            if "기한" in text and any(token in text for token in ("지난", "초과")): return [self._call("list_tasks", view="overdue")]
            if "우선순위" in text and "높" in text: return [self._call("list_tasks", view="high_priority")]
            if "진행 중" in text: return [self._call("list_tasks", view="active")]
            if "완료" in text: return [self._call("list_tasks", view="done")]
            return [self._call("list_tasks", view="all")]
        return []

    def _choose_tools(self, text: str) -> list[ToolCall]:
        if calls := self._choose_task_memo_tools(text):
            return calls
        if "메모" in text:  # unsupported memo phrasing must not fall through to mail/calendar rules
            return []
        if any(token in text for token in ("등록", "추가", "만들")) and any(token in text for token in ("회의", "일정")):
            return [self._call("propose_calendar_event", title="회의", date="금요일", time="14:00")]
        if "메일" in text and any(token in text for token in ("미확인", "안 읽", "읽지", "몇")):
            return [self._call("get_unread_mail_count")]
        if "메일" in text and any(token in text for token in ("찾", "검색")):
            match = re.search(r"(\S+)\s*(?:관련\s*)?메일", text)
            return [self._call("search_mail", query=_clean(match[1]) if match and match[1] not in ("받은", "최근") else "")]
        if any(token in text for token in ("오늘", "신경", "해야", "할 일")):
            return [self._call("get_today_events"), self._call("list_tasks")]
        if "내일" in text and "일정" in text: return [self._call("get_tomorrow_events")]
        if "메일" in text: return [self._call("get_unread_mail_count")]
        return []

    @staticmethod
    def _direct_response(text: str) -> str:
        if "날씨" in text:
            return "현재 날씨 정보를 조회할 수 있는 Tool이 연결되어 있지 않습니다."
        if "메모" in text and any(token in text for token in ("작성", "저장", "써")):
            return "메모로 남길 제목과 내용을 알려 주세요. (Mock 모드에서는 대화 내용을 요약해 저장할 수 없습니다.)"
        if any(token in text for token in ("안녕", "누구", "소개")):
            return "안녕하세요. 저는 일정, 업무, 메모를 정리하고 업무를 놓치지 않도록 돕는 workMate입니다."
        return "도와드릴 업무를 말씀해 주세요. 일정, 업무, 메모는 연결된 도구로 확인할 수 있습니다."

    @staticmethod
    def _summarize_task_memo(by_name: dict[str, Any]) -> str | None:
        for name, value in by_name.items():
            if not (name in {"list_tasks", "list_memos", "get_memo", "list_events"} or str(name).startswith(("propose_task", "propose_memo", "propose_calendar"))):
                continue
            if isinstance(value, dict) and value.get("error"): return f"처리하지 못했습니다: {value['error']}"
            if isinstance(value, dict) and value.get("status") == "needs_clarification":
                lines = [f"- {item['title']}" + (f" (마감 {item['due_at']})" if item.get("due_at") else f" ({item['when']})" if item.get("when") else "") for item in value["candidates"]]
                return "일치하는 항목이 여러 개입니다. 어느 것인지 알려 주세요.\n" + "\n".join(lines)
            if isinstance(value, dict) and value.get("status") in {"no_change", "unsupported"}: return value["message"]
            if name == "list_events":
                if not value: return "해당 기간에 일정이 없습니다."
                return f"일정 {len(value)}건입니다.\n" + "\n".join(f"- {e['title']} · {e['when']}" + (" · 반복" if e.get("is_recurring") else "") for e in value[:20])
            if name == "list_tasks":
                if not value: return "조건에 맞는 업무가 없습니다."
                return f"업무 {len(value)}건입니다.\n" + "\n".join(f"- {t['title']} · {PRIORITY_LABEL.get(t['priority'], t['priority'])} · {STATUS_LABEL.get(t['status'], t['status'])} · 마감 {_kst(t.get('due_at'))}" for t in value[:15])
            if name == "list_memos":
                if not value: return "조건에 맞는 메모가 없습니다."
                return f"메모 {len(value)}건입니다.\n" + "\n".join(f"- {m['title']} · 수정 {_kst(m['updated_at'])}" for m in value)
            if name == "get_memo": return f"‘{value['title']}’ 메모 내용:\n{value['content'] or '(내용 없음)'}"
            return f"{value['title']} 요청을 준비했습니다. 아래 카드를 확인하고 승인하면 반영됩니다. 승인 전에는 아무것도 변경되지 않습니다."
        return None

    @staticmethod
    def _summarize(outputs: list[dict[str, Any]]) -> str:
        by_name = {item.get("name"): json.loads(item.get("output", "{}")) for item in outputs}
        if "get_today_events" in by_name:
            events, tasks = by_name.get("get_today_events", []), by_name.get("list_tasks", [])
            open_tasks = [task for task in tasks if task.get("status") not in ("DONE", "CANCELLED")]
            first_event = events[0] if events else None
            event_text = f"오늘 {first_event['starts_at'][11:16]}에 {first_event['title']}가 있습니다." if first_event else "오늘 일정은 없습니다."
            return f"{event_text}\n진행 중인 업무는 {len(open_tasks)}건입니다."
        failed = next((v for v in by_name.values() if isinstance(v, dict) and v.get("success") is False and v.get("message")), None)
        if failed: return failed["message"]
        if "get_unread_mail_count" in by_name: return f"미확인 메일은 {by_name['get_unread_mail_count'].get('unread_count', 0)}건입니다."
        if "search_mail" in by_name: return f"메일 {by_name['search_mail'].get('count', 0)}건을 찾았습니다. 목록은 화면에서 확인해 주세요."
        if "get_mail_status" in by_name: return by_name["get_mail_status"].get("message", "")
        if task_memo := MockLLMProvider._summarize_task_memo(by_name): return task_memo
        if "get_tomorrow_events" in by_name: return "내일 일정 조회 결과를 확인했습니다."
        if "propose_calendar_event" in by_name: return "일정 등록 제안을 준비했습니다. 승인 전에는 캘린더가 변경되지 않습니다."
        return "요청을 처리했습니다."

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server.api.dependencies import agent, calendar_action_service, calendar_provider, conversation_service, dashboard_service, knowledge_service, local_action_service, mail_action_service, mail_service, memo_service, task_service
from server.providers.mail.base import MailApiError, MailDetail, MailError, MailFolder, MailNotConnected, MailPage, MailSendUncertain, MailSessionExpired, MailSettingError, MailStatus
from server.services.mail import MAX_ATTACHMENT_BYTES, StagedAttachment
from server.services.memos import UnknownTaskError
from server.models.schemas import ActionCard, ActionExecutionResult, CalendarConnectionStatus, ChatRequest, ChatResponse, ConversationDetail, ConversationSummary, Dashboard, KnowledgeArtifact, KnowledgeArtifactDetail, KnowledgeSource, KnowledgeSourceCreate, Memo, MemoCreate, MemoUpdate, SyncRun, Task, TaskCreate, TaskUpdate, TaskView

router = APIRouter(prefix="/api/v1")


@router.get("/dashboard", response_model=Dashboard)
def dashboard() -> Dashboard:
    return dashboard_service().get_dashboard()


@router.get("/tasks", response_model=list[Task])
def list_tasks(view: TaskView = "all", q: Annotated[str | None, Query(max_length=200)] = None) -> list[Task]:
    return task_service().list_tasks(view, q)


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str) -> Task:
    task = task_service().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: str, payload: TaskUpdate) -> Task:
    try:
        task = task_service().update_task(task_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str) -> Response:
    if not task_service().delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=204)


@router.post("/tasks/{task_id}/reopen", response_model=Task)
def reopen_task(task_id: str) -> Task:
    task = task_service().reopen_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/memos", response_model=list[Memo])
def list_memos(q: Annotated[str | None, Query(max_length=200)] = None, task_id: str | None = None) -> list[Memo]:
    return memo_service().list_memos(q, task_id)


@router.post("/memos", response_model=Memo, status_code=201)
def create_memo(payload: MemoCreate) -> Memo:
    try:
        return memo_service().create_memo(payload)
    except UnknownTaskError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/memos/{memo_id}", response_model=Memo)
def get_memo(memo_id: str) -> Memo:
    memo = memo_service().get_memo(memo_id)
    if not memo:
        raise HTTPException(status_code=404, detail="Memo not found")
    return memo


@router.patch("/memos/{memo_id}", response_model=Memo)
def update_memo(memo_id: str, payload: MemoUpdate) -> Memo:
    try:
        memo = memo_service().update_memo(memo_id, payload)
    except UnknownTaskError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not memo:
        raise HTTPException(status_code=404, detail="Memo not found")
    return memo


@router.delete("/memos/{memo_id}", status_code=204)
def delete_memo(memo_id: str) -> Response:
    if not memo_service().delete_memo(memo_id):
        raise HTTPException(status_code=404, detail="Memo not found")
    return Response(status_code=204)


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(payload: TaskCreate) -> Task:
    return task_service().create_task(payload)


@router.post("/tasks/{task_id}/complete", response_model=Task)
def complete_task(task_id: str) -> Task:
    task = task_service().complete_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    return conversation_service().chat(payload.message, payload.conversation_id)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations() -> list[ConversationSummary]:
    return conversation_service().list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> ConversationDetail:
    detail = conversation_service().get(conversation_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> Response:
    if not conversation_service().delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/calendar/status", response_model=CalendarConnectionStatus)
def calendar_status() -> CalendarConnectionStatus:
    return calendar_provider().status()


@router.post("/calendar/connect", response_model=CalendarConnectionStatus)
def calendar_connect() -> CalendarConnectionStatus:
    try:
        return calendar_provider().connect()
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/calendar/verify", response_model=CalendarConnectionStatus)
def calendar_verify() -> CalendarConnectionStatus:
    try:
        return calendar_provider().verify()
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/calendar/disconnect", response_model=CalendarConnectionStatus)
def calendar_disconnect() -> CalendarConnectionStatus:
    return calendar_provider().disconnect()


def _action_owner(action_id: str):
    for service in (local_action_service(), calendar_action_service(), mail_action_service()):
        if service.has(action_id):
            return service
    raise HTTPException(status_code=404, detail="Action not found")


@router.post("/actions/{action_id}/approve", response_model=ActionExecutionResult)
def approve_action(action_id: str) -> ActionExecutionResult:
    result = _action_owner(action_id).approve(action_id)
    conversation_service().record_decision(result)
    return result


@router.post("/actions/{action_id}/reject", response_model=ActionExecutionResult)
def reject_action(action_id: str) -> ActionExecutionResult:
    result = _action_owner(action_id).reject(action_id)
    conversation_service().record_decision(result)
    return result


@router.get("/knowledge/sources", response_model=list[KnowledgeSource])
def list_knowledge_sources() -> list[KnowledgeSource]:
    return knowledge_service().list_sources()


@router.post("/knowledge/sources", response_model=KnowledgeSource, status_code=201)
def create_knowledge_source(payload: KnowledgeSourceCreate) -> KnowledgeSource:
    try:
        return knowledge_service().create_source(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/knowledge/sources/{source_id}/scan", response_model=SyncRun)
def scan_knowledge_source(source_id: str, background_tasks: BackgroundTasks) -> SyncRun:
    try:
        run = knowledge_service().start_scan(source_id)
        background_tasks.add_task(knowledge_service().scan, source_id, run.id)
        return run
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/knowledge/sync-runs/{run_id}", response_model=SyncRun)
def knowledge_sync_run(run_id: str) -> SyncRun:
    try: return knowledge_service().get_run(run_id)
    except KeyError as error: raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/knowledge/sync-runs/{run_id}/cancel", response_model=SyncRun)
def cancel_knowledge_sync_run(run_id: str) -> SyncRun:
    try: return knowledge_service().cancel(run_id)
    except KeyError as error: raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/knowledge/sources/{source_id}/artifacts", response_model=list[KnowledgeArtifact])
def list_knowledge_artifacts(source_id: str) -> list[KnowledgeArtifact]:
    return knowledge_service().list_artifacts(source_id)


@router.get("/knowledge/artifacts/{artifact_id}", response_model=KnowledgeArtifactDetail)
def knowledge_artifact(artifact_id: str) -> KnowledgeArtifactDetail:
    try:
        return knowledge_service().artifact(artifact_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ----------------------------- mail -----------------------------
# Mail content moves only between the mail server, this backend and the screen. None of these routes is reachable by the model.
def _mail_http(error: MailError) -> HTTPException:
    if isinstance(error, MailNotConnected):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, MailSessionExpired):
        return HTTPException(status_code=401, detail=str(error))
    if isinstance(error, (MailApiError, MailSettingError, MailSendUncertain)):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))  # plain MailError = rejected input


class MailDraftRequest(BaseModel):
    mode: str = "new"
    folder: str | None = None
    ukey: str | None = None


class MailSendRequest(BaseModel):
    to: list[str] = Field(default_factory=list, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    bcc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=200000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    mode: str = "new"
    ref_folder: str | None = None
    ref_ukey: str | None = None
    save_draft: bool = False
    reserve_at: str | None = None


class MailKeysRequest(BaseModel):
    folder: str
    ukeys: list[str] = Field(min_length=1, max_length=50)
    labels: list[str] = Field(default_factory=list, max_length=50)


class MailMoveRequest(MailKeysRequest):
    target_folder: str


class MailReadRequest(MailKeysRequest):
    read: bool


@router.get("/mail/status", response_model=MailStatus)
def mail_status() -> MailStatus:
    return mail_service().status()


@router.get("/mail/folders", response_model=list[MailFolder])
def mail_folders() -> list[MailFolder]:
    try:
        return mail_service().folders()
    except MailError as error:
        raise _mail_http(error) from error


@router.get("/mail/messages", response_model=MailPage)
def mail_messages(folder: str, page: int = 1, size: int = 25, unread: bool = False, q: Annotated[str | None, Query(max_length=200)] = None) -> MailPage:
    try:
        return mail_service().messages(folder, page, size, unread, q)
    except MailError as error:
        raise _mail_http(error) from error


@router.get("/mail/messages/{ukey}", response_model=MailDetail)
def mail_message(ukey: str, folder: str) -> MailDetail:
    try:
        return mail_service().message(folder, ukey)
    except MailError as error:
        raise _mail_http(error) from error


@router.get("/mail/messages/{ukey}/attachments/{aindex}")
def mail_attachment(ukey: str, aindex: str, folder: str, mindex: str = "-1") -> StreamingResponse:
    try:
        stream = mail_service().download(folder, ukey, aindex, mindex)
    except MailError as error:
        raise _mail_http(error) from error
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(stream.name)}", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    return StreamingResponse(stream.chunks, media_type="application/octet-stream", headers=headers)  # never trust the server's content type


@router.post("/mail/drafts")
def mail_draft(payload: MailDraftRequest) -> dict:
    try:
        return mail_service().build_draft(payload.mode, payload.folder, payload.ukey)
    except MailError as error:
        raise _mail_http(error) from error


@router.post("/mail/attachments", response_model=StagedAttachment, status_code=201)
async def mail_stage_attachment(request: Request, filename: str) -> StagedAttachment:
    """Local staging only: nothing is uploaded to the mail server until a send is approved."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="첨부파일이 너무 큽니다.")
    data = await request.body()
    try:
        return mail_service().stage(filename, data)
    except MailError as error:
        raise _mail_http(error) from error


@router.delete("/mail/attachments/{staged_id}", status_code=204)
def mail_discard_attachment(staged_id: str) -> Response:
    mail_service().discard(staged_id)
    return Response(status_code=204)


def _propose(build) -> ActionCard:
    try:
        return build()
    except MailError as error:
        raise _mail_http(error) from error


@router.post("/mail/actions/send", response_model=ActionCard)
def mail_propose_send(p: MailSendRequest) -> ActionCard:
    return _propose(lambda: mail_action_service().propose_send(p.to, p.cc, p.bcc, p.subject, p.body, p.attachment_ids, p.mode, p.ref_folder, p.ref_ukey, p.save_draft, p.reserve_at))


@router.post("/mail/actions/delete", response_model=ActionCard)
def mail_propose_delete(p: MailKeysRequest) -> ActionCard:
    return _propose(lambda: mail_action_service().propose_delete(p.folder, p.ukeys, p.labels))


@router.post("/mail/actions/move", response_model=ActionCard)
def mail_propose_move(p: MailMoveRequest) -> ActionCard:
    return _propose(lambda: mail_action_service().propose_move(p.folder, p.target_folder, p.ukeys, p.labels))


@router.post("/mail/actions/read", response_model=ActionCard)
def mail_propose_read(p: MailReadRequest) -> ActionCard:
    return _propose(lambda: mail_action_service().propose_set_read(p.folder, p.ukeys, p.read, p.labels))

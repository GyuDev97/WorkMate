import type { ActionCard, ActionExecutionResult, CalendarConnectionStatus, ChatResponse, ConversationDetail, ConversationSummary, Dashboard, KnowledgeArtifact, KnowledgeArtifactDetail, KnowledgeSource, MailDetail, MailDraft, MailFolder, MailPage, MailStatus, Memo, MemoInput, StagedAttachment, SyncRun, Task, TaskInput, TaskView } from './types'

const baseUrl = import.meta.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8765'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!response.ok) {
    const problem = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(problem?.detail ?? (response.status === 0 ? 'Backend Offline' : `요청 처리에 실패했습니다 (${response.status}).`))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; service: string }>('/health'),
  dashboard: () => request<Dashboard>('/api/v1/dashboard'),
  chat: (message: string, conversationId?: string | null) => request<ChatResponse>('/api/v1/chat', { method: 'POST', body: JSON.stringify({ message, ...(conversationId ? { conversation_id: conversationId } : {}) }) }),
  conversations: () => request<ConversationSummary[]>('/api/v1/conversations'),
  conversation: (id: string) => request<ConversationDetail>(`/api/v1/conversations/${id}`),
  deleteConversation: (id: string) => request<void>(`/api/v1/conversations/${id}`, { method: 'DELETE' }),
  createTask: (title: string, priority: string) => request<Task>('/api/v1/tasks', { method: 'POST', body: JSON.stringify({ title, priority }) }),
  tasks: (view: TaskView, q: string) => request<Task[]>(`/api/v1/tasks?${new URLSearchParams({ view, ...(q.trim() ? { q: q.trim() } : {}) })}`),
  createTaskDetail: (input: TaskInput) => request<Task>('/api/v1/tasks', { method: 'POST', body: JSON.stringify(input) }),
  updateTask: (id: string, input: TaskInput) => request<Task>(`/api/v1/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteTask: (id: string) => request<void>(`/api/v1/tasks/${id}`, { method: 'DELETE' }),
  reopenTask: (id: string) => request<Task>(`/api/v1/tasks/${id}/reopen`, { method: 'POST' }),
  memos: (q: string) => request<Memo[]>(`/api/v1/memos${q.trim() ? `?${new URLSearchParams({ q: q.trim() })}` : ''}`),
  createMemo: (input: MemoInput) => request<Memo>('/api/v1/memos', { method: 'POST', body: JSON.stringify(input) }),
  updateMemo: (id: string, input: MemoInput) => request<Memo>(`/api/v1/memos/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteMemo: (id: string) => request<void>(`/api/v1/memos/${id}`, { method: 'DELETE' }),
  completeTask: (id: string) => request<Task>(`/api/v1/tasks/${id}/complete`, { method: 'POST' }),
  calendarStatus: () => request<CalendarConnectionStatus>('/api/v1/calendar/status'),
  connectCalendar: () => request<CalendarConnectionStatus>('/api/v1/calendar/connect', { method: 'POST' }),
  verifyCalendar: () => request<CalendarConnectionStatus>('/api/v1/calendar/verify', { method: 'POST' }),
  disconnectCalendar: () => request<CalendarConnectionStatus>('/api/v1/calendar/disconnect', { method: 'POST' }),
  mailStatus: () => request<MailStatus>('/api/v1/mail/status'),
  mailFolders: () => request<MailFolder[]>('/api/v1/mail/folders'),
  mailMessages: (folder: string, page: number, query: string, unread: boolean) => request<MailPage>(`/api/v1/mail/messages?${new URLSearchParams({ folder, page: String(page), size: '25', ...(query.trim() ? { q: query.trim() } : {}), ...(unread ? { unread: 'true' } : {}) })}`),
  mailMessage: (folder: string, ukey: string) => request<MailDetail>(`/api/v1/mail/messages/${encodeURIComponent(ukey)}?${new URLSearchParams({ folder })}`),
  mailDraft: (mode: string, folder?: string, ukey?: string) => request<MailDraft>('/api/v1/mail/drafts', { method: 'POST', body: JSON.stringify({ mode, folder, ukey }) }),
  mailStage: async (file: File) => {
    const response = await fetch(`${baseUrl}/api/v1/mail/attachments?${new URLSearchParams({ filename: file.name })}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } })
    if (!response.ok) { const problem = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(problem?.detail ?? `첨부하지 못했습니다 (${response.status}).`) }
    return response.json() as Promise<StagedAttachment>
  },
  mailDiscard: (id: string) => request<void>(`/api/v1/mail/attachments/${id}`, { method: 'DELETE' }),
  mailAttachmentUrl: (folder: string, ukey: string, aindex: string, mindex: string) => `${baseUrl}/api/v1/mail/messages/${encodeURIComponent(ukey)}/attachments/${encodeURIComponent(aindex)}?${new URLSearchParams({ folder, mindex })}`,
  mailProposeSend: (body: Record<string, unknown>) => request<ActionCard>('/api/v1/mail/actions/send', { method: 'POST', body: JSON.stringify(body) }),
  mailProposeDelete: (folder: string, ukeys: string[], labels: string[]) => request<ActionCard>('/api/v1/mail/actions/delete', { method: 'POST', body: JSON.stringify({ folder, ukeys, labels }) }),
  mailProposeMove: (folder: string, target_folder: string, ukeys: string[], labels: string[]) => request<ActionCard>('/api/v1/mail/actions/move', { method: 'POST', body: JSON.stringify({ folder, target_folder, ukeys, labels }) }),
  mailProposeRead: (folder: string, ukeys: string[], read: boolean, labels: string[]) => request<ActionCard>('/api/v1/mail/actions/read', { method: 'POST', body: JSON.stringify({ folder, ukeys, read, labels }) }),
  approveAction: (id: string) => request<ActionExecutionResult>(`/api/v1/actions/${id}/approve`, { method: 'POST' }),
  rejectAction: (id: string) => request<ActionExecutionResult>(`/api/v1/actions/${id}/reject`, { method: 'POST' }),
  knowledgeSources: () => request<KnowledgeSource[]>('/api/v1/knowledge/sources'),
  createKnowledgeSource: (name: string, local_path: string) => request<KnowledgeSource>('/api/v1/knowledge/sources', { method: 'POST', body: JSON.stringify({ name, local_path }) }),
  scanKnowledgeSource: (id: string) => request<SyncRun>(`/api/v1/knowledge/sources/${id}/scan`, { method: 'POST' }),
  knowledgeSyncRun: (id: string) => request<SyncRun>(`/api/v1/knowledge/sync-runs/${id}`),
  cancelKnowledgeSyncRun: (id: string) => request<SyncRun>(`/api/v1/knowledge/sync-runs/${id}/cancel`, { method: 'POST' }),
  knowledgeArtifacts: (id: string) => request<KnowledgeArtifact[]>(`/api/v1/knowledge/sources/${id}/artifacts`),
  knowledgeArtifact: (id: string) => request<KnowledgeArtifactDetail>(`/api/v1/knowledge/artifacts/${id}`)
}

export type Priority = 'HIGH' | 'MEDIUM' | 'LOW'
export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'DONE' | 'CANCELLED'

export interface Event { id: string; title: string; starts_at: string; ends_at?: string; description: string; all_day: boolean; location: string; calendar_id: string; source: string; is_recurring?: boolean }
export interface Mail { id: string; sender: string; subject: string; received_at: string; needs_reply: boolean; preview: string }
export interface Task { id: string; title: string; description: string; priority: Priority; start_at?: string | null; due_at?: string | null; status: TaskStatus; source: string; created_at: string; updated_at: string }
export type TaskView = 'all' | 'active' | 'done' | 'overdue' | 'due_today' | 'high_priority'
export interface TaskInput { title?: string; description?: string; priority?: Priority; start_at?: string | null; due_at?: string | null; status?: TaskStatus }
export interface TaskSummary { total: number; active: number; done: number; overdue: number; due_today: number; high_priority: number }
export interface PriorityItem { task_id: string; title: string; reason: 'overdue' | 'due_today' | 'high_priority'; priority: Priority; due_at?: string | null }
export interface Memo { id: string; title: string; content: string; task_id?: string | null; task_title?: string | null; created_at: string; updated_at: string }
export interface MemoInput { title?: string; content?: string; task_id?: string | null }
export interface Provider { name: string; state: 'connected' | 'mock' | 'offline'; detail: string }
export interface Activity { id: string; occurred_at: string; action: string; detail: string; category: string }
export interface Dashboard {
  briefing: { greeting: string; date_label: string; overview: string[]; priorities: string[]; priority_items: PriorityItem[]; task_summary: TaskSummary }
  events: Event[]; mails: Mail[]; tasks: Task[]; providers: Provider[]; activities: Activity[]
  calendar: { state: 'mock' | 'connected' | 'not_connected' | 'error'; detail: string }
  mail: MailState
}
export type ApprovalStatus = 'not_required' | 'pending_approval' | 'executing' | 'completed' | 'failed' | 'rejected' | 'expired' | 'needs_check'
export interface ActionCard { action_id?: string; kind: 'mail_draft' | 'calendar_event' | 'calendar_update' | 'calendar_delete' | 'task' | 'task_create' | 'task_update' | 'task_complete' | 'task_delete' | 'memo_create' | 'memo_update' | 'memo_delete' | 'mail_send' | 'mail_delete' | 'mail_move' | 'mail_read'; title: string; fields: Record<string, string>; requires_confirmation: boolean; status: ApprovalStatus }
export interface CalendarConnectionStatus { provider: 'mock' | 'google'; state: 'connected' | 'mock' | 'offline'; detail: string; calendar_id: string }
export interface ActionExecutionResult { action: ActionCard; message: string }
export interface ToolTrace { tool: string; risk: 'READ' | 'WRITE_LOCAL' | 'PROPOSED_LOCAL_WRITE' | 'PROPOSED_EXTERNAL_WRITE'; success: boolean; duration_ms: number }
export interface ChatResponse { conversation_id?: string; ui_results?: UiResult[]; message: string; sources: string[]; action?: ActionCard; trace: ToolTrace[] }
export interface KnowledgeSource { id: string; name: string; local_path: string; created_at: string; last_synced_at?: string; artifact_count: number }
export interface KnowledgeArtifact { id: string; source_id: string; title: string; relative_path: string; size_bytes: number; modified_at: string; chunk_count: number; parsed: boolean }
export interface KnowledgeArtifactDetail extends KnowledgeArtifact { content: string }
export interface SyncRun { id: string; source_id: string; status: string; total: number; scanned: number; created: number; updated: number; unchanged: number; deleted: number; parsed: number; failed: number; chunks_created: number; error?: string; started_at: string; finished_at?: string }

export interface ConversationSummary { id: string; title: string; created_at: string; updated_at: string; message_count: number }
export interface StoredMessage { id: string; seq: number; role: 'user' | 'assistant'; content: string; trace: ToolTrace[]; sources: string[]; action?: ActionCard | null; created_at: string }
export interface ConversationDetail extends ConversationSummary { messages: StoredMessage[] }

export type MailProviderState = 'mock' | 'connected' | 'not_connected' | 'session_expired' | 'error'
export interface MailState { provider: 'none' | 'mock' | 'bizmeka'; state: MailProviderState; detail: string; unread_count?: number | null }
export interface MailStatus { provider: 'none' | 'mock' | 'bizmeka'; state: MailProviderState; detail: string; live_enabled: boolean }
export interface MailFolder { key: string; name: string; total: number; unread: number; kind: 'inbox' | 'sent' | 'trash' | 'draft' | 'spam' | 'other' | 'custom' }
export interface MailListItem { ukey: string; folder: string; subject: string; sender_name: string; sender_address: string; recipients: string; sent_at: string; unread: boolean; important: boolean; answered: boolean }
export interface MailPage { items: MailListItem[]; page: number; page_size: number; total_pages: number }
export interface MailAttachment { aindex: string; mindex: string; name: string; size?: number | null }
export interface MailDetail { ukey: string; folder: string; subject: string; from_name: string; from_address: string; to: string; cc: string; bcc: string; sent_at: string; body_html: string; body_text: string; attachments: MailAttachment[] }
export interface MailDraft { mode: 'new' | 'reply' | 'forward'; to: string[]; cc: string[]; bcc: string[]; subject: string; body: string; ref_folder: string | null; ref_ukey: string | null; note?: string }
export interface StagedAttachment { id: string; name: string; size: number }
/** Display-only payloads from tools (mail lists). They go to the screen, never to the model, and are not stored. */
export type UiResult = { kind: 'mail_list'; query: string; mock: boolean; items: MailListItem[]; more_pages: boolean } | { kind: 'mail_unread'; unread_count: number; mock: boolean } | { kind: 'mail_status'; state: string; detail: string; mock: boolean } | { kind: 'mail_error'; message: string }

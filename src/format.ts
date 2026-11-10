export function fmtTime(value: string) { return new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Seoul' }).format(new Date(value)) }
export function fmtDate(value?: string | null) { return value ? new Intl.DateTimeFormat('ko-KR', { month: 'short', day: 'numeric' }).format(new Date(value)) : '기한 없음' }
export function seoulDateKey(value: Date | string = new Date()) {
  const date = typeof value === 'string' ? new Date(value) : value
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')}`
}
export function fmtDateTime(value: string) { return new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) }

/** ISO string -> value for <input type="datetime-local"> in the user's local time zone. */
export function toLocalInput(value?: string | null) {
  if (!value) return ''
  const date = new Date(value); const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
export function fromLocalInput(value: string) { return value ? new Date(value).toISOString() : null }

const pad2 = (n: number) => String(n).padStart(2, '0')
function dotted(value: string) { const d = new Date(value); return `${d.getFullYear()}.${pad2(d.getMonth() + 1)}.${pad2(d.getDate())}` }
/** Task period: "2026.11.05 ~ 2026.11.10", "2026.11.05부터", "2026.11.10까지"; null when neither date is set. */
export function fmtPeriod(task: { start_at?: string | null; due_at?: string | null }) {
  if (task.start_at && task.due_at) return `${dotted(task.start_at)} ~ ${dotted(task.due_at)}`
  if (task.start_at) return `${dotted(task.start_at)}부터`
  if (task.due_at) return `${dotted(task.due_at)}까지`
  return null
}

export function isLate(task: { status: string; due_at?: string | null }) { return task.status !== 'DONE' && task.status !== 'CANCELLED' && !!task.due_at && new Date(task.due_at).getTime() < Date.now() }

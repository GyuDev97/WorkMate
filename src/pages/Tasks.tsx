import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Task, TaskStatus, TaskSummary, TaskView } from '../types'
import { fmtPeriod, fromLocalInput, isLate, toLocalInput } from '../format'
import { Empty, ErrorNote, Modal, PageHeader } from '../ui'

const views: { id: TaskView; label: string; count: (s: TaskSummary) => number }[] = [
  { id: 'all', label: '전체', count: (s) => s.total }, { id: 'active', label: '진행 중', count: (s) => s.active },
  { id: 'done', label: '완료', count: (s) => s.done }, { id: 'overdue', label: '기한 초과', count: (s) => s.overdue }
]
const statusLabel: Record<TaskStatus, string> = { TODO: '할 일', IN_PROGRESS: '진행 중', DONE: '완료', CANCELLED: '취소' }
const priorityLabel = { HIGH: '높음', MEDIUM: '보통', LOW: '낮음' }

function TaskModal({ task, onClose, onSaved }: { task: Task | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const [title, setTitle] = useState(task?.title ?? ''); const [description, setDescription] = useState(task?.description ?? '')
  const [priority, setPriority] = useState(task?.priority ?? 'MEDIUM'); const [start, setStart] = useState(toLocalInput(task?.start_at)); const [due, setDue] = useState(toLocalInput(task?.due_at))
  const [status, setStatus] = useState<TaskStatus>(task?.status ?? 'TODO'); const [saving, setSaving] = useState(false); const [error, setError] = useState('')
  async function save(event: FormEvent) {
    event.preventDefault(); if (!title.trim()) { setError('업무 제목을 입력해 주세요.'); return }
    if (start && due && new Date(start).getTime() > new Date(due).getTime()) { setError('시작일은 종료일보다 늦을 수 없습니다.'); return }
    setSaving(true); setError('')
    try {
      const input = { title: title.trim(), description, priority, start_at: fromLocalInput(start), due_at: fromLocalInput(due) }
      if (task) await api.updateTask(task.id, { ...input, status }); else await api.createTaskDetail(input)
      await onSaved(); onClose()
    } catch (e) { setError(e instanceof Error ? e.message : '저장하지 못했습니다.'); setSaving(false) }
  }
  return <Modal title={task ? '업무 수정' : '업무 추가'} onClose={onClose}><form className="form-grid" onSubmit={save}>
    <label>제목<input autoFocus maxLength={180} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: API 문서 확인" /></label>
    <label>상세 내용<textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
    <div className="form-row">
      <label>우선순위<select value={priority} onChange={(e) => setPriority(e.target.value as Task['priority'])}><option value="HIGH">높음</option><option value="MEDIUM">보통</option><option value="LOW">낮음</option></select></label>
    </div>
    <div className="form-row">
      <label>시작일<span className="date-field"><input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />{start && <button type="button" className="text-button" onClick={() => setStart('')} aria-label="시작일 지우기">지우기</button>}</span></label>
      <label>종료일(마감)<span className="date-field"><input type="datetime-local" value={due} min={start || undefined} onChange={(e) => setDue(e.target.value)} />{due && <button type="button" className="text-button" onClick={() => setDue('')} aria-label="종료일 지우기">지우기</button>}</span></label>
    </div>
    {task && <label>상태<select value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>{(Object.keys(statusLabel) as TaskStatus[]).map((s) => <option key={s} value={s}>{statusLabel[s]}</option>)}</select></label>}
    <ErrorNote text={error} />
    <div className="action-buttons"><button type="button" className="secondary" onClick={onClose}>취소</button><button className="primary" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div>
  </form></Modal>
}

export function Tasks({ summary, initialView, refresh }: { summary: TaskSummary; initialView: TaskView; refresh: () => Promise<void> }) {
  const [view, setView] = useState<TaskView>(initialView); const [query, setQuery] = useState(''); const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState(''); const [editing, setEditing] = useState<Task | 'new' | null>(null)
  const [title, setTitle] = useState(''); const [priority, setPriority] = useState('MEDIUM'); const [saving, setSaving] = useState(false)
  const load = useCallback(async () => { try { setTasks(await api.tasks(view, query)); setError('') } catch (e) { setError(e instanceof Error ? e.message : '업무를 불러오지 못했습니다.') } }, [view, query])
  useEffect(() => { const timer = window.setTimeout(() => void load(), query ? 250 : 0); return () => window.clearTimeout(timer) }, [load, query])
  const changed = async () => { await Promise.all([load(), refresh()]) }
  async function act(run: () => Promise<unknown>) { try { await run(); await changed() } catch (e) { setError(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.') } }
  async function add(event: FormEvent) { event.preventDefault(); if (!title.trim()) return; setSaving(true); try { await api.createTask(title.trim(), priority); setTitle(''); await changed() } catch (e) { setError(e instanceof Error ? e.message : '추가하지 못했습니다.') } finally { setSaving(false) } }
  function remove(task: Task) { if (window.confirm(`‘${task.title}’ 업무를 삭제할까요?\n삭제한 업무는 복구할 수 없습니다. 연결된 메모는 유지됩니다.`)) void act(() => api.deleteTask(task.id)) }
  return <><PageHeader title="업무" text="일정과 구분하여, 완료할 일을 관리합니다." action={<button className="primary" onClick={() => setEditing('new')}>+ 업무 추가</button>} />
    <section className="tasks-layout"><article className="panel task-list">
      <div className="toolbar"><div className="tabs" role="tablist">{views.map((v) => <button key={v.id} role="tab" aria-selected={view === v.id} className={view === v.id ? 'active' : ''} onClick={() => setView(v.id)}>{v.label}<small>{v.count(summary)}</small></button>)}</div>
        <input className="search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="제목·내용 검색" aria-label="업무 검색" /></div>
      <ErrorNote text={error} />
      {tasks === null ? (error ? null : <Empty text="업무를 불러오는 중…" />) : tasks.length ? tasks.map((task) => {
        const done = task.status === 'DONE'; const late = isLate(task)
        return <div className={`task-row ${done ? 'done' : ''}`} key={task.id}>
          <button className="check" onClick={() => void act(() => (done ? api.reopenTask(task.id) : api.completeTask(task.id)))} aria-label={done ? '완료 취소' : '완료 처리'} title={done ? '완료 취소' : '완료 처리'}>{done ? '✓' : ''}</button>
          <div><b>{task.title}</b>{task.description && <p>{task.description}</p>}<small className={late ? 'late' : ''}>{late ? '기한 초과 · ' : ''}{fmtPeriod(task) ?? '기한 없음'} · {task.status === 'IN_PROGRESS' || task.status === 'CANCELLED' ? `${statusLabel[task.status]} · ` : ''}{task.source === 'manual' ? '직접 추가' : task.source}</small></div>
          <span className="row-end"><span className={`priority ${task.priority.toLowerCase()}`}>{priorityLabel[task.priority]}</span>
            <button className="text-button" onClick={() => setEditing(task)}>수정</button><button className="text-button danger-text" onClick={() => remove(task)}>삭제</button></span></div>
      }) : <Empty text={query ? '검색 결과가 없습니다.' : '해당하는 업무가 없습니다.'} />}
    </article>
    <aside className="panel task-create"><h2>빠른 업무 추가</h2><p>제목만 입력해 바로 추가합니다. 마감일·내용은 ‘수정’에서 입력하세요.</p><form onSubmit={add}><label>업무 내용<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: API 문서 확인" /></label><label>우선순위<select value={priority} onChange={(e) => setPriority(e.target.value)}><option value="HIGH">높음</option><option value="MEDIUM">보통</option><option value="LOW">낮음</option></select></label><button className="primary full" disabled={saving}>{saving ? '추가 중…' : '업무 추가'}</button></form></aside></section>
    {editing && <TaskModal task={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={changed} />}
  </>
}

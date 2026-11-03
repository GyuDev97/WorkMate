import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Memo, Task } from '../types'
import { fmtDateTime } from '../format'
import { Empty, ErrorNote, PageHeader } from '../ui'

export function Memos() {
  const [memos, setMemos] = useState<Memo[] | null>(null); const [tasks, setTasks] = useState<Task[]>([]); const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Memo | 'new' | null>(null)
  const [title, setTitle] = useState(''); const [content, setContent] = useState(''); const [taskId, setTaskId] = useState('')
  const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState('')
  // Only the newest list request may write state, so a slow earlier response cannot overwrite a fresher list.
  const latest = useRef(0)
  const load = useCallback(async () => { const id = ++latest.current; try { const next = await api.memos(query); if (id !== latest.current) return; setMemos(next); setError('') } catch (e) { setError(e instanceof Error ? e.message : '메모를 불러오지 못했습니다.') } }, [query])
  useEffect(() => { const timer = window.setTimeout(() => void load(), query ? 250 : 0); return () => window.clearTimeout(timer) }, [load, query])
  useEffect(() => { void api.tasks('all', '').then(setTasks).catch(() => setTasks([])) }, [])
  const dirty = selected === 'new' ? !!(title || content || taskId) : !!selected && (title !== selected.title || content !== selected.content || taskId !== (selected.task_id ?? ''))
  function open(next: Memo | 'new' | null) {
    if (dirty && !window.confirm('저장하지 않은 변경 사항이 있습니다. 버리고 이동할까요?')) return
    setSelected(next); setNotice(''); setError('')
    setTitle(next && next !== 'new' ? next.title : ''); setContent(next && next !== 'new' ? next.content : ''); setTaskId(next && next !== 'new' ? next.task_id ?? '' : '')
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!selected) return
    if (!title.trim()) { setError('메모 제목을 입력해 주세요.'); return }
    setSaving(true); setError(''); setNotice('')
    try {
      const input = { title: title.trim(), content, task_id: taskId || null }
      const saved = selected === 'new' ? await api.createMemo(input) : await api.updateMemo(selected.id, input)
      setSelected(saved); setTitle(saved.title); setNotice('저장했습니다.')
      setMemos((current) => [saved, ...(current ?? []).filter((memo) => memo.id !== saved.id)]); await load()
    } catch (e) { setError(e instanceof Error ? e.message : '저장하지 못했습니다.') } finally { setSaving(false) }
  }
  async function remove() {
    if (!selected || selected === 'new') { open(null); return }
    if (!window.confirm(`‘${selected.title}’ 메모를 삭제할까요?\n삭제한 메모는 복구할 수 없습니다.`)) return
    try { await api.deleteMemo(selected.id); setSelected(null); setTitle(''); setContent(''); setTaskId('')
      setMemos((current) => (current ?? []).filter((memo) => memo.id !== selected.id)); await load() } catch (e) { setError(e instanceof Error ? e.message : '삭제하지 못했습니다.') }
  }
  return <><PageHeader title="메모" text="업무와 관계없이 떠오르는 내용을 바로 적어 둡니다." action={<button className="primary" onClick={() => open('new')}>+ 새 메모</button>} />
    <section className="memo-layout">
      <aside className="panel memo-list"><input className="search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="제목·본문 검색" aria-label="메모 검색" />
        <div className="memo-items">{memos === null ? (error ? null : <Empty text="메모를 불러오는 중…" />) : memos.length ? memos.map((memo) =>
          <button key={memo.id} className={selected !== 'new' && selected?.id === memo.id ? 'selected' : ''} onClick={() => open(memo)}><b>{memo.title}</b><small>{memo.content.replace(/\s+/g, ' ').slice(0, 60) || '내용 없음'}</small><small>{fmtDateTime(memo.updated_at)}{memo.task_title ? ` · ✓ ${memo.task_title}` : ''}</small></button>
        ) : <Empty text={query ? '검색 결과가 없습니다.' : '아직 메모가 없습니다. ‘새 메모’로 시작하세요.'} />}</div>
        {!selected && <ErrorNote text={error} />}</aside>
      <article className="panel memo-editor">{selected ? <form className="form-grid" onSubmit={save}>
        <label>제목<input maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="메모 제목" /></label>
        <label>본문<textarea rows={14} value={content} onChange={(e) => setContent(e.target.value)} placeholder="내용을 입력하세요" /></label>
        <label>연결할 업무 (선택)<select value={taskId} onChange={(e) => setTaskId(e.target.value)}><option value="">연결 안 함</option>{tasks.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
        {selected !== 'new' && <small className="meta">작성 {fmtDateTime(selected.created_at)} · 수정 {fmtDateTime(selected.updated_at)}</small>}
        <ErrorNote text={error} />{notice && <p className="knowledge-notice">{notice}</p>}
        <div className="action-buttons"><button type="button" className="secondary danger-text" onClick={() => void remove()}>{selected === 'new' ? '취소' : '삭제'}</button><button className="primary" disabled={saving || !dirty}>{saving ? '저장 중…' : '저장'}</button></div>
      </form> : <Empty text="왼쪽에서 메모를 선택하거나 ‘새 메모’를 눌러 시작하세요." />}</article>
    </section></>
}

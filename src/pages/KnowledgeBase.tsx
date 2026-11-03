import { FormEvent, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { KnowledgeArtifact, KnowledgeArtifactDetail, KnowledgeSource } from '../types'
import { fmtDateTime } from '../format'
import { Empty, ErrorNote, Modal, PageHeader } from '../ui'

function RegisterModal({ onClose, onCreated }: { onClose: () => void; onCreated: (source: KnowledgeSource) => Promise<void> }) {
  const [name, setName] = useState(''); const [path, setPath] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!name.trim() || !path.trim()) { setError('표시 이름과 폴더 경로를 모두 입력해 주세요.'); return }
    setBusy(true); setError('')
    try { const source = await api.createKnowledgeSource(name.trim(), path.trim()); await onCreated(source); onClose() } catch (e) { setError(e instanceof Error ? e.message : '폴더를 등록하지 못했습니다.'); setBusy(false) }
  }
  return <Modal title="지식 폴더 등록" onClose={onClose}><form className="form-grid" onSubmit={submit}>
    <p className="modal-hint">등록한 폴더만 읽습니다. 원본 파일은 변경하지 않습니다.</p>
    <label>표시 이름<input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 업무 문서" /></label>
    <label>로컬 폴더 경로<input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/me/Documents/work" /></label>
    <ErrorNote text={error} /><div className="action-buttons"><button type="button" className="secondary" onClick={onClose}>취소</button><button className="primary" disabled={busy}>{busy ? '등록 중…' : '폴더 등록'}</button></div>
  </form></Modal>
}

// Folders can hold thousands of documents; render in pages to keep the list responsive.
const PAGE = 300

export function KnowledgeBase() {
  const [sources, setSources] = useState<KnowledgeSource[] | null>(null); const [selected, setSelected] = useState<KnowledgeSource | null>(null)
  const [artifacts, setArtifacts] = useState<KnowledgeArtifact[] | null>(null); const [document, setDocument] = useState<KnowledgeArtifactDetail | null>(null)
  const [filter, setFilter] = useState(''); const [notice, setNotice] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const [limit, setLimit] = useState(PAGE); const [runId, setRunId] = useState<string | null>(null); const [registering, setRegistering] = useState(false); const [previewLoading, setPreviewLoading] = useState(false)
  const fail = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback)
  const loadSources = async () => { const next = await api.knowledgeSources(); setSources(next); return next }
  async function select(source: KnowledgeSource) {
    setSelected(source); setDocument(null); setArtifacts(null); setFilter(''); setError(''); setNotice(''); setLimit(PAGE)
    try { setArtifacts(await api.knowledgeArtifacts(source.id)) } catch (e) { fail(e, '문서 목록을 불러오지 못했습니다.'); setArtifacts([]) }
  }
  // Initial source synchronization intentionally begins after the knowledge screen mounts.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void loadSources().catch((e) => { fail(e, '지식베이스를 불러오지 못했습니다.'); setSources([]) }) }, [])
  async function scan() {
    if (!selected) return; const source = selected; setBusy(true); setError(''); setNotice('수집 대상을 확인하는 중입니다…')
    try {
      const run = await api.scanKnowledgeSource(source.id); setRunId(run.id)
      const poll = async () => {
        const current = await api.knowledgeSyncRun(run.id)
        if (current.status === 'running') { setNotice(current.total ? `수집 중: ${current.scanned} / ${current.total}개 파일 (${Math.round((current.scanned / current.total) * 100)}%)` : '수집 대상을 확인하는 중입니다…'); window.setTimeout(() => void poll().catch((e) => { fail(e, '수집 상태를 확인하지 못했습니다.'); setBusy(false); setRunId(null) }), 1000); return }
        setBusy(false); setRunId(null)
        const next = await loadSources(); const fresh = next.find((s) => s.id === source.id) ?? source; await select(fresh)
        if (current.status === 'failed' || current.error) { setNotice(''); setError(current.error ?? '수집에 실패했습니다.') }
        else setNotice(current.status === 'cancelled' ? `수집을 중단했습니다. ${current.scanned} / ${current.total}개 파일 처리됨` : `수집 완료: ${current.scanned}개 확인 · ${current.parsed}개 파싱 · ${current.chunks_created}개 청크${current.failed ? ` · ${current.failed}개 실패` : ''}`)
      }
      void poll().catch((e) => { fail(e, '수집 상태를 확인하지 못했습니다.'); setBusy(false); setRunId(null) })
    } catch (e) { fail(e, '수집에 실패했습니다.'); setNotice(''); setBusy(false) }
  }
  async function cancel() { if (!runId) return; try { await api.cancelKnowledgeSyncRun(runId); setNotice('중단 요청을 보냈습니다. 현재 파일 처리가 끝나면 멈춥니다.') } catch (e) { fail(e, '중단하지 못했습니다.') } }
  async function open(artifact: KnowledgeArtifact) { setPreviewLoading(true); setError(''); try { setDocument(await api.knowledgeArtifact(artifact.id)) } catch (e) { fail(e, '문서를 열 수 없습니다.') } finally { setPreviewLoading(false) } }
  const shown = useMemo(() => (artifacts ?? []).filter((a) => `${a.title} ${a.relative_path}`.toLowerCase().includes(filter.trim().toLowerCase())), [artifacts, filter])
  return <><PageHeader title="지식베이스" text="로컬 폴더의 문서를 안전하게 수집하고 원문을 확인합니다." action={<button className="primary" onClick={() => setRegistering(true)}>+ 폴더 등록</button>} />
    <section className="kb-layout">
      <aside className="panel kb-sources"><h2>지식 폴더</h2>
        <div className="source-list">{sources === null ? <Empty text="불러오는 중…" /> : sources.length ? sources.map((source) =>
          <button key={source.id} className={selected?.id === source.id ? 'selected' : ''} onClick={() => void select(source)}><b>{source.name}</b>
            <small><span className={`state-chip ${source.last_synced_at ? 'ok' : ''}`}>{source.last_synced_at ? '수집됨' : '미수집'}</span> {source.artifact_count}개 문서</small>
            {source.last_synced_at && <small>최근 수집 {fmtDateTime(source.last_synced_at)}</small>}</button>
        ) : <Empty text="등록된 폴더가 없습니다. ‘폴더 등록’으로 시작하세요." />}</div></aside>
      <div className="kb-main">
        {selected ? <>
          <article className="panel kb-head"><div className="panel-head"><div><h2>{selected.name}</h2><p className="path">{selected.local_path}</p></div><div className="action-buttons">{runId && <button className="secondary" onClick={() => void cancel()}>수집 중단</button>}<button className="primary" disabled={busy} onClick={() => void scan()}>{busy ? '수집 중…' : '수집·파싱'}</button></div></div>
            {notice && <p className="knowledge-notice">{notice}</p>}<ErrorNote text={error} /></article>
          <div className="kb-panes">
            <article className="panel kb-docs"><input className="search" type="search" value={filter} onChange={(e) => { setFilter(e.target.value); setLimit(PAGE) }} placeholder="문서 이름·경로 필터" aria-label="문서 필터" />
              <div className="artifact-list">{artifacts === null ? <Empty text="문서 목록을 불러오는 중…" /> : shown.length ? <>{shown.slice(0, limit).map((artifact) =>
                <button key={artifact.id} className={document?.id === artifact.id ? 'selected' : ''} onClick={() => void open(artifact)}><span>▤</span><div><b>{artifact.title}</b><small>{artifact.relative_path} · {artifact.chunk_count ? `${artifact.chunk_count}개 청크` : '추출 실패 또는 미파싱'}</small></div></button>
              )}{shown.length > limit && <button className="more" onClick={() => setLimit(limit + PAGE)}>더 보기 ({limit} / {shown.length})</button>}</> : <Empty text={filter ? '일치하는 문서가 없습니다.' : '아직 수집된 문서가 없습니다. ‘수집·파싱’을 눌러 주세요.'} />}</div></article>
            <article className="panel kb-preview">{previewLoading ? <Empty text="문서를 여는 중…" /> : document ? <><div className="panel-head"><div><h2>{document.title}</h2><p>{document.relative_path} · {document.chunk_count}개 청크 · {Math.max(1, Math.round(document.size_bytes / 1024))}KB</p></div></div><pre>{document.content || '추출된 텍스트가 없습니다.'}</pre></> : <Empty text="문서를 선택하면 추출된 텍스트를 미리 볼 수 있습니다." />}</article>
          </div></> : <article className="panel"><ErrorNote text={error} /><Empty text="왼쪽에서 폴더를 선택하세요." /></article>}
      </div></section>
    {registering && <RegisterModal onClose={() => setRegistering(false)} onCreated={async (source) => { const next = await loadSources(); await select(next.find((s) => s.id === source.id) ?? source); setNotice('폴더를 등록했습니다. 수집·파싱 버튼으로 문서를 읽어 오세요.') }} />}
  </>
}

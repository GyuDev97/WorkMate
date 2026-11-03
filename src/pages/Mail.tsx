import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { ActionPreview } from '../ActionPreview'
import type { ActionCard, MailDetail, MailDraft, MailFolder, MailListItem, MailPage, MailStatus, StagedAttachment } from '../types'
import { Empty, ErrorNote, Modal, PageHeader } from '../ui'

const split = (value: string) => value.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean)
const fmtSize = (size?: number | null) => size == null ? '' : size < 1024 ? `${size}B` : size < 1048576 ? `${Math.round(size / 1024)}KB` : `${(size / 1048576).toFixed(1)}MB`
const frameDoc = (html: string) => `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{font:13px/1.65 -apple-system,sans-serif;color:#41506a;margin:0;padding:4px}a{color:#4a7fe8}blockquote{border-left:3px solid #dfe5ef;margin:8px 0;padding-left:10px;color:#6b7a93}pre{white-space:pre-wrap}</style>${html}`

function ActionDialog({ card, onClose, onDone }: { card: ActionCard; onClose: () => void; onDone: () => Promise<void> }) {
  const [current, setCurrent] = useState(card); const [busy, setBusy] = useState(false); const [message, setMessage] = useState(''); const [error, setError] = useState('')
  async function decide(approve: boolean) {
    if (!current.action_id || busy) return
    setBusy(true); setError('')
    try { const result = approve ? await api.approveAction(current.action_id) : await api.rejectAction(current.action_id); setCurrent(result.action); setMessage(result.message); await onDone() }
    catch (e) { setError(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.') } finally { setBusy(false) }
  }
  return <Modal title="실행 전 확인" onClose={onClose}>
    <ActionPreview action={current} busy={busy} onApprove={() => void decide(true)} onReject={() => void decide(false)} />
    {message && <p className="knowledge-notice">{message}</p>}<ErrorNote text={error} />
    {current.status !== 'pending_approval' && <div className="action-buttons"><button className="primary" onClick={onClose}>닫기</button></div>}
  </Modal>
}

function Compose({ draft, onClose, onCard }: { draft: MailDraft; onClose: () => void; onCard: (card: ActionCard) => void }) {
  const [to, setTo] = useState(draft.to.join(', ')); const [cc, setCc] = useState(draft.cc.join(', ')); const [bcc, setBcc] = useState(draft.bcc.join(', '))
  const [subject, setSubject] = useState(draft.subject); const [body, setBody] = useState(draft.body); const [files, setFiles] = useState<StagedAttachment[]>([])
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const picker = useRef<HTMLInputElement>(null)
  async function attach(list: FileList | null) {
    if (!list) return; setError('')
    for (const file of Array.from(list)) { try { const staged = await api.mailStage(file); setFiles((current) => [...current, staged]) } catch (e) { setError(e instanceof Error ? e.message : '첨부하지 못했습니다.') } }
    if (picker.current) picker.current.value = ''
  }
  async function submit(event: FormEvent, saveDraft: boolean) {
    event.preventDefault(); setBusy(true); setError('')
    try { onCard(await api.mailProposeSend({ to: split(to), cc: split(cc), bcc: split(bcc), subject, body, attachment_ids: files.map((f) => f.id), mode: draft.mode, ref_folder: draft.ref_folder, ref_ukey: draft.ref_ukey, save_draft: saveDraft })) }
    catch (e) { setError(e instanceof Error ? e.message : '요청을 만들지 못했습니다.') } finally { setBusy(false) }
  }
  async function close() { await Promise.all(files.map((f) => api.mailDiscard(f.id).catch(() => undefined))); onClose() }
  return <Modal title={draft.mode === 'reply' ? '답장 작성' : draft.mode === 'forward' ? '전달 작성' : '새 메일'} onClose={() => void close()}>
    <form className="form-grid" onSubmit={(e) => void submit(e, false)}>
      <p className="modal-hint">‘발송 요청’을 누르면 확인 카드가 열리고, 승인하기 전에는 아무것도 전송되지 않습니다.{draft.note ? ` ${draft.note}` : ''}</p>
      <label>받는사람<input autoFocus value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com, …" /></label>
      <div className="form-row"><label>참조(CC)<input value={cc} onChange={(e) => setCc(e.target.value)} /></label><label>숨은참조(BCC)<input value={bcc} onChange={(e) => setBcc(e.target.value)} /></label></div>
      <label>제목<input maxLength={300} value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
      <label>본문<textarea rows={10} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <div className="attach-row"><input ref={picker} type="file" multiple hidden onChange={(e) => void attach(e.target.files)} /><button type="button" className="secondary" onClick={() => picker.current?.click()}>+ 파일 첨부</button>
        {files.map((f) => <span className="chip" key={f.id}>{f.name} · {fmtSize(f.size)}<button type="button" aria-label="첨부 제거" onClick={() => { void api.mailDiscard(f.id); setFiles((c) => c.filter((x) => x.id !== f.id)) }}>✕</button></span>)}</div>
      <ErrorNote text={error} />
      <div className="action-buttons"><button type="button" className="secondary" onClick={() => void close()}>취소</button><button type="button" className="secondary" disabled={busy} onClick={(e) => void submit(e as unknown as FormEvent, true)}>임시보관 요청</button><button className="primary" disabled={busy}>{busy ? '준비 중…' : '발송 요청'}</button></div>
    </form></Modal>
}

export function Mail({ refresh }: { refresh: () => Promise<void> }) {
  const [status, setStatus] = useState<MailStatus | null>(null); const [folders, setFolders] = useState<MailFolder[]>([]); const [folder, setFolder] = useState<string | null>(null)
  const [page, setPage] = useState(1); const [query, setQuery] = useState(''); const [unreadOnly, setUnreadOnly] = useState(false)
  const [list, setList] = useState<MailPage | null>(null); const [selected, setSelected] = useState<MailListItem | null>(null); const [detail, setDetail] = useState<MailDetail | null>(null)
  const [error, setError] = useState(''); const [loading, setLoading] = useState(false); const [compose, setCompose] = useState<MailDraft | null>(null); const [card, setCard] = useState<ActionCard | null>(null)
  const [moveTarget, setMoveTarget] = useState('')
  const usable = status?.state === 'mock' || status?.state === 'connected'
  const fail = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback)

  const loadFolders = useCallback(async () => { try { const next = await api.mailFolders(); setFolders(next); setFolder((current) => current ?? next.find((f) => f.kind === 'inbox')?.key ?? next[0]?.key ?? null); setError('') } catch (e) { fail(e, '메일함을 불러오지 못했습니다.') } }, [])
  // Status first: nothing else is requested while mail is not connected.
  useEffect(() => { void api.mailStatus().then((s) => { setStatus(s); if (s.state === 'mock' || s.state === 'connected') void loadFolders() }).catch((e) => fail(e, '메일 상태를 확인하지 못했습니다.')) }, [loadFolders])
  const loadList = useCallback(async () => {
    if (!folder) return; setLoading(true)
    try { setList(await api.mailMessages(folder, page, query, unreadOnly)); setError('') } catch (e) { fail(e, '메일 목록을 불러오지 못했습니다.'); setList(null) } finally { setLoading(false) }
  }, [folder, page, query, unreadOnly])
  useEffect(() => { const timer = window.setTimeout(() => void loadList(), query ? 300 : 0); return () => window.clearTimeout(timer) }, [loadList, query])
  async function open(item: MailListItem) { setSelected(item); setDetail(null); setError(''); try { setDetail(await api.mailMessage(item.folder, item.ukey)) } catch (e) { fail(e, '메일을 열 수 없습니다.') } }
  async function startDraft(mode: string) { try { setCompose(await api.mailDraft(mode, selected?.folder, selected?.ukey)) } catch (e) { fail(e, '초안을 만들지 못했습니다.') } }
  async function propose(build: () => Promise<ActionCard>) { try { setCard(await build()) } catch (e) { fail(e, '요청을 만들지 못했습니다.') } }
  async function download(attachment: MailDetail['attachments'][number]) {
    if (!detail) return
    try {
      const response = await fetch(api.mailAttachmentUrl(detail.folder, detail.ukey, attachment.aindex, attachment.mindex))
      if (!response.ok) { const problem = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(problem?.detail ?? '첨부파일을 받지 못했습니다.') }
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = attachment.name.replace(/[\\/]/g, '_'); link.click(); URL.revokeObjectURL(url)
    } catch (e) { fail(e, '첨부파일을 받지 못했습니다.') }
  }
  const afterAction = async () => { await Promise.all([loadFolders(), loadList(), refresh()]); setSelected(null); setDetail(null) }
  const labels = selected ? [selected.subject] : []
  const mock = status?.state === 'mock'
  return <><PageHeader title="메일" text="사내 메일을 조회하고, 변경은 확인 카드를 승인한 뒤에만 실행합니다." action={usable ? <button className="primary" onClick={() => void startDraft('new')}>+ 새 메일</button> : undefined} />
    {status === null ? (error ? <ErrorNote text={error} /> : <Empty text="메일 상태를 확인하는 중…" />)
      : !usable ? <article className="panel"><div className="empty calendar-state">메일이 연결되지 않았습니다.<small>{status.detail}</small></div></article>
      : <>
        {mock && <p className="mock-banner">Mock 데이터입니다. 실제 회사 메일이 아니며, 표시되는 내용은 개발용 샘플입니다.</p>}
        <ErrorNote text={error} />
        <section className="mail-layout">
          <aside className="panel mail-folders"><h2>메일함</h2>{folders.map((f) => <button key={f.key} className={f.key === folder ? 'selected' : ''} onClick={() => { setFolder(f.key); setPage(1); setSelected(null); setDetail(null) }}><span>{f.name}</span>{f.unread > 0 && <b>{f.unread}</b>}</button>)}</aside>
          <article className="panel mail-list">
            <div className="toolbar"><input className="search" type="search" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1) }} placeholder="제목·본문 검색" aria-label="메일 검색" />
              <button className={`secondary ${unreadOnly ? 'on' : ''}`} onClick={() => { setUnreadOnly(!unreadOnly); setPage(1) }}>안읽음만</button></div>
            <div className="mail-items">{loading && !list ? <Empty text="불러오는 중…" /> : list?.items.length ? list.items.map((item) => <button key={item.ukey} className={`mail-item ${item.unread ? 'unread' : ''} ${selected?.ukey === item.ukey ? 'selected' : ''}`} onClick={() => void open(item)}>
              <b>{item.sender_name || item.sender_address || '(발신자 없음)'}{item.important && <i className="imp">중요</i>}</b><strong>{item.subject}</strong><small>{item.sent_at}</small></button>) : <Empty text={query ? '검색 결과가 없습니다.' : '메일이 없습니다.'} />}</div>
            {list && list.total_pages > 1 && <div className="pager"><button className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>이전</button><span>{page} / {list.total_pages}</span><button className="secondary" disabled={page >= list.total_pages} onClick={() => setPage(page + 1)}>다음</button></div>}
          </article>
          <article className="panel mail-detail">{!selected ? <Empty text="메일을 선택하면 내용이 표시됩니다." /> : !detail ? (error ? null : <Empty text="메일을 여는 중…" />) : <>
            <div className="panel-head"><div><h2>{detail.subject}</h2><p>{detail.from_name} &lt;{detail.from_address}&gt; · {detail.sent_at}</p><p>받는사람: {detail.to}{detail.cc ? ` · 참조: ${detail.cc}` : ''}</p></div></div>
            <div className="mail-actions"><button className="secondary" onClick={() => void startDraft('reply')}>답장</button><button className="secondary" onClick={() => void startDraft('reply_all')}>전체답장</button><button className="secondary" onClick={() => void startDraft('forward')}>전달</button>
              <button className="secondary" onClick={() => void propose(() => api.mailProposeRead(detail.folder, [detail.ukey], selected.unread, labels))}>{selected.unread ? '읽음 표시' : '안읽음 표시'}</button>
              <select value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)} aria-label="이동할 메일함"><option value="">이동…</option>{folders.filter((f) => f.key !== detail.folder).map((f) => <option key={f.key} value={f.key}>{f.name}</option>)}</select>
              <button className="secondary" disabled={!moveTarget} onClick={() => void propose(() => api.mailProposeMove(detail.folder, moveTarget, [detail.ukey], labels))}>이동 요청</button>
              <button className="secondary danger-text" onClick={() => void propose(() => api.mailProposeDelete(detail.folder, [detail.ukey], labels))}>삭제(휴지통)</button></div>
            <iframe className="mail-body" title="메일 본문" sandbox="" srcDoc={frameDoc(detail.body_html || '<p>(본문 없음)</p>')} />
            {detail.attachments.length > 0 && <div className="attach-row">{detail.attachments.map((a) => <button key={a.aindex} className="chip as-button" onClick={() => void download(a)}>⬇ {a.name}{a.size ? ` · ${fmtSize(a.size)}` : ''}</button>)}</div>}
          </>}</article>
        </section></>}
    {compose && <Compose draft={compose} onClose={() => setCompose(null)} onCard={(next) => { setCompose(null); setCard(next) }} />}
    {card && <ActionDialog card={card} onClose={() => setCard(null)} onDone={afterAction} />}
  </>
}

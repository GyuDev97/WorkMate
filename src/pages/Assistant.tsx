import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ActionCard, ConversationSummary, Dashboard, StoredMessage, UiResult } from '../types'
import { fmtDate, fmtTime } from '../format'
import { Logo, PageHeader, StatusDot } from '../ui'
import { ActionPreview } from '../ActionPreview'

type UiMessage = { key: string; role: 'assistant' | 'user'; text: string; action?: ActionCard | null; sources?: string[]; trace?: StoredMessage['trace']; ui?: UiResult[] }
const GREETING = '안녕하세요. 일정과 업무, 메모를 확인하고 정리해 드릴게요. 무엇을 도와드릴까요?'
const prompts = ['오늘 뭐 해야 돼?', '오늘 마감인 업무 알려줘', '진행 중인 업무 보여줘', '최근 작성한 메모 보여줘', '내일 일정 알려줘']
function MailResults({ results, openMail }: { results: UiResult[]; openMail: () => void }) {
  return <>{results.map((result, index) => result.kind === 'mail_list' ? <div className="mail-results" key={index}>
    {result.mock && <small className="mock-tag">Mock 데이터 · 실제 회사 메일이 아닙니다</small>}
    {result.items.length ? result.items.map((item) => <div className="row" key={item.ukey}><b>{item.subject}</b>{item.sender_name || item.sender_address} · {item.sent_at}{item.unread ? ' · 안읽음' : ''}</div>) : <small className="more">검색 결과가 없습니다.</small>}
    {result.more_pages && <small className="more">더 많은 결과가 있습니다.</small>}<button className="text-button" onClick={openMail}>메일 화면에서 열기 →</button></div>
    : result.kind === 'mail_unread' ? <div className="mail-results" key={index}><div className="row"><b>미확인 메일 {result.unread_count}건</b>{result.mock ? 'Mock 데이터' : '실제 메일'}</div><button className="text-button" onClick={openMail}>메일 화면에서 열기 →</button></div>
    : result.kind === 'mail_error' ? <div className="mail-results" key={index}><div className="row"><b>메일 처리 안내</b>{result.message}</div></div> : null)}</>
}

const fromStored = (message: StoredMessage): UiMessage => ({ key: message.id, role: message.role, text: message.content, action: message.action, sources: message.sources, trace: message.trace })

type Props = { data: Dashboard; refresh: () => Promise<void>; openMail: () => void; conversationId: string | null | undefined; setConversationId: (id: string | null) => void; draft: string; setDraft: (value: string) => void }

export function Assistant({ data, refresh, openMail, conversationId, setConversationId, draft, setDraft }: Props) {
  const [messages, setMessages] = useState<UiMessage[]>([]); const [conversations, setConversations] = useState<ConversationSummary[] | null>(null)
  const [loading, setLoading] = useState(false); const [restoring, setRestoring] = useState(false); const [approving, setApproving] = useState<string | null>(null); const [error, setError] = useState('')
  const scroller = useRef<HTMLDivElement>(null); const skipLoad = useRef<string | null>(null)
  const loadList = useCallback(async () => { try { const list = await api.conversations(); setConversations(list); return list } catch { setConversations([]); return [] } }, [])
  // First visit of a session: reopen the most recent conversation; an explicit "new conversation" (null) stays empty.
  // Mount-only on purpose: later changes of conversationId must not re-pick the latest conversation.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void loadList().then((list) => { if (conversationId === undefined) setConversationId(list[0]?.id ?? null) }) }, [])
  useEffect(() => {
    if (!conversationId) { setMessages([]); return }
    if (skipLoad.current === conversationId) { skipLoad.current = null; return }
    let cancelled = false; setRestoring(true); setError('')
    api.conversation(conversationId).then((detail) => { if (!cancelled) setMessages(detail.messages.map(fromStored)) })
      .catch((e) => { if (cancelled) return; if (e instanceof Error && /404|not found/i.test(e.message)) setConversationId(null); else setError(e instanceof Error ? e.message : '대화를 불러오지 못했습니다.') })
      .finally(() => { if (!cancelled) setRestoring(false) })
    return () => { cancelled = true }
  }, [conversationId, setConversationId])
  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight }, [messages, loading, restoring])

  async function submit(event: FormEvent) {
    event.preventDefault(); const message = draft.trim(); if (!message || loading || restoring) return
    setDraft(''); setError(''); setMessages((current) => [...current, { key: `local-${Date.now()}`, role: 'user', text: message }]); setLoading(true)
    try {
      const response = await api.chat(message, conversationId)
      if (response.conversation_id && response.conversation_id !== conversationId) { skipLoad.current = response.conversation_id; setConversationId(response.conversation_id) }
      setMessages((current) => [...current, { key: `local-${Date.now()}-a`, role: 'assistant', text: response.message, action: response.action, sources: response.sources, trace: response.trace, ui: response.ui_results }])
      void loadList()
    } catch (e) { setMessages((current) => [...current, { key: `local-${Date.now()}-e`, role: 'assistant', text: e instanceof Error ? `연결 오류: ${e.message}` : '요청을 처리하지 못했습니다.' }]) } finally { setLoading(false) }
  }
  async function decide(action: ActionCard, run: (id: string) => ReturnType<typeof api.approveAction>) {
    if (!action.action_id || approving) return
    setApproving(action.action_id)
    try {
      const result = await run(action.action_id)
      setMessages((current) => [...current.map((message) => message.action?.action_id === action.action_id ? { ...message, action: result.action } : message), { key: `local-${Date.now()}-r`, role: 'assistant', text: result.message }])
      await refresh(); void loadList()
    } catch (e) {
      setMessages((current) => [...current, { key: `local-${Date.now()}-x`, role: 'assistant', text: `요청을 처리하지 못했습니다. 변경되었는지 확인해 주세요. (${e instanceof Error ? e.message : '알 수 없는 오류'})` }])
    } finally { setApproving(null) }
  }
  async function remove(conversation: ConversationSummary) {
    if (!window.confirm(`‘${conversation.title}’ 대화를 삭제할까요?\n저장된 메시지가 모두 삭제되며 복구할 수 없습니다. 업무·메모·일정은 영향받지 않습니다.`)) return
    try { await api.deleteConversation(conversation.id); if (conversation.id === conversationId) setConversationId(null); await loadList() } catch (e) { setError(e instanceof Error ? e.message : '삭제하지 못했습니다.') }
  }
  const busy = loading || restoring
  const next = data.events.find((event) => new Date(event.starts_at).getTime() >= Date.now())
  return <><PageHeader title="메이트" text="말로 요청하면 상황을 확인하고 필요한 작업을 준비합니다." action={<button className="secondary" disabled={busy || (!conversationId && messages.length === 0)} onClick={() => { setConversationId(null); setDraft('') }}>+ 새 대화</button>} />
    <section className="assistant-layout"><article className="panel conversation"><div className="conversation-head"><span><Logo /></span><div><b>workMate 메이트</b><small><StatusDot state="connected" />준비됨 · Tool Calling</small></div></div>
      <div className="messages" ref={scroller}>
        {restoring && <div className="empty">대화를 불러오는 중…</div>}
        {!restoring && messages.length === 0 && <div className="message assistant"><div className="bubble"><p>{GREETING}</p></div></div>}
        {messages.map((message) => <div className={`message ${message.role}`} key={message.key}><div className="bubble">{message.text.split('\n').map((line, lineIndex) => <p key={lineIndex}>{line || <br />}</p>)}{message.action && <ActionPreview action={message.action} onApprove={(current) => void decide(current, api.approveAction)} onReject={(current) => void decide(current, api.rejectAction)} busy={approving === message.action.action_id} />}{message.ui?.length ? <MailResults results={message.ui} openMail={openMail} /> : message.trace?.some((trace) => trace.tool === 'search_mail' || trace.tool === 'get_unread_mail_count') ? <small className="sources">메일 내용은 보안상 저장되지 않아 다시 표시되지 않습니다. 메일 화면에서 확인하세요.</small> : null}{message.trace?.length ? <small className="sources">{message.trace.map((trace) => `${trace.success ? '✓' : '!'} ${trace.tool}`).join(' · ')}</small> : null}{message.sources?.length ? <small className="sources">참조: {message.sources.join(' · ')}</small> : null}</div></div>)}
        {loading && <div className="message assistant"><div className="bubble typing"><i /><i /><i /></div></div>}
        {error && <p className="error-note" role="alert">{error}</p>}
      </div>
      <form className="composer" onSubmit={submit}><input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="workMate에게 요청하세요…" /><button className="send" type="submit" disabled={busy}>↑</button></form></article>
      <aside className="assistant-side">
        <article className="panel compact conv-panel"><div className="conv-head"><h2>대화 기록</h2></div>
          <div className="conv-list">{conversations === null ? <small className="conv-empty">불러오는 중…</small> : conversations.length ? conversations.map((conversation) => <div key={conversation.id} className={`conv-item ${conversation.id === conversationId ? 'selected' : ''}`}>
            <button className="conv-open" disabled={busy} onClick={() => setConversationId(conversation.id)}><b>{conversation.title}</b><small>{fmtDate(conversation.updated_at)} {fmtTime(conversation.updated_at)} · {conversation.message_count}개</small></button>
            <button className="conv-delete" aria-label="대화 삭제" disabled={busy} onClick={() => void remove(conversation)}>✕</button></div>) : <small className="conv-empty">저장된 대화가 없습니다.</small>}</div></article>
        <article className="panel compact"><h2>빠른 질문</h2><p>자주 쓰는 요청으로 바로 시작하세요.</p><div className="prompt-list">{prompts.map((prompt) => <button key={prompt} onClick={() => setDraft(prompt)}>{prompt}<span>→</span></button>)}</div></article>
        <article className="panel compact next-event"><p className="eyebrow">NEXT UP</p><h3>{next?.title ?? '다음 일정이 없습니다.'}</h3><b>{next ? fmtTime(next.starts_at) : ''}</b><small>{next ? 'Calendar Provider 기준' : '예정된 일정 없음'}</small></article></aside></section></>
}

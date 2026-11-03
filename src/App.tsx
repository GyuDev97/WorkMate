import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { CalendarConnectionStatus, Dashboard, PriorityItem, TaskView } from './types'
import { fmtDate, fmtTime } from './format'
import { Logo, PageHeader, StatusDot } from './ui'
import { Assistant } from './pages/Assistant'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Mail } from './pages/Mail'
import { Memos } from './pages/Memos'
import { Tasks } from './pages/Tasks'

type Page = 'home' | 'assistant' | 'tasks' | 'memos' | 'mail' | 'knowledge' | 'activity' | 'settings'
const nav: { id: Page; icon: string; label: string }[] = [
  { id: 'home', icon: '⌂', label: '홈' }, { id: 'assistant', icon: '✦', label: '메이트' },
  { id: 'tasks', icon: '✓', label: '업무' }, { id: 'memos', icon: '✎', label: '메모' }, { id: 'mail', icon: '✉', label: '메일' }, { id: 'knowledge', icon: '▤', label: '지식베이스' }, { id: 'activity', icon: '◷', label: '활동 기록' }
]




const reasonLabel: Record<PriorityItem['reason'], string> = { overdue: '기한 초과', due_today: '오늘 마감', high_priority: '높은 우선순위' }

function CalendarBadge({ state }: { state: Dashboard['calendar']['state'] }) { return state === 'connected' ? <span className="source-badge live">Google Calendar</span> : state === 'mock' ? <span className="source-badge mock">Mock 데이터</span> : state === 'error' ? <span className="source-badge error">오류</span> : <span className="source-badge off">미연결</span> }

function MailBadge({ state }: { state: Dashboard['mail']['state'] }) { return state === 'connected' ? <span className="source-badge live">실제 데이터</span> : state === 'mock' ? <span className="source-badge mock">Mock 데이터</span> : state === 'error' || state === 'session_expired' ? <span className="source-badge error">{state === 'session_expired' ? '재인증 필요' : '오류'}</span> : <span className="source-badge off">미연결</span> }

function Home({ data, move }: { data: Dashboard; move: (page: Page, view?: TaskView) => void }) {
  const summary = data.briefing.task_summary; const items = data.briefing.priority_items
  const stats: [string, string, number, string, TaskView][] = [
    ['✓', '진행 중 업무', summary.active, 'task', 'active'], ['◷', '오늘 마감', summary.due_today, 'calendar', 'active'],
    ['!', '기한 초과', summary.overdue, 'attention', 'overdue'], ['▲', '높은 우선순위', summary.high_priority, 'mail', 'active']
  ]
  return <>
    <PageHeader title={data.briefing.greeting} text={`${data.briefing.date_label} · 업무 현황은 저장된 업무 데이터 기준입니다.`} />
    <section className="stat-grid">{stats.map(([icon, label, value, theme, view]) => <button className="stat-card" key={label} onClick={() => move('tasks', view)}><span className={`stat-icon ${theme}`}>{icon}</span><span><b>{value}</b><small>{label}</small></span><i>›</i></button>)}</section>
    <section className="content-grid home-grid">
      <article className="panel schedule-panel"><div className="panel-head"><div><h2>오늘 일정 <CalendarBadge state={data.calendar.state} /></h2><p>집중해야 할 시간입니다.</p></div><button className="text-button" onClick={() => move('assistant')}>일정 물어보기</button></div>
        {data.calendar.state === 'not_connected' ? <div className="empty calendar-state">Google Calendar가 연결되지 않았습니다.<small>{data.calendar.detail}</small><button className="secondary" onClick={() => move('settings')}>설정에서 연결</button></div>
          : data.calendar.state === 'error' ? <div className="empty calendar-state error">일정을 불러오지 못했습니다.<small>{data.calendar.detail}</small><button className="secondary" onClick={() => move('settings')}>연결 확인</button></div>
          : data.events.length ? <div className="timeline">{data.events.map((event, index) => <div className="event" key={event.id}><time>{event.all_day ? '종일' : fmtTime(event.starts_at)}</time><span className={`event-line ${index === 0 ? 'now' : ''}`} /><div><strong>{event.title}{event.is_recurring && <small className="recurring"> 반복</small>}</strong><p>{event.all_day ? '하루 종일' : event.ends_at ? `${fmtTime(event.starts_at)} – ${fmtTime(event.ends_at)}` : ''}</p></div></div>)}</div> : <div className="empty">오늘 일정이 없습니다.</div>}
      </article>
      <article className="panel focus-panel"><div className="panel-head"><div><h2>우선 확인 <span className="source-badge live">실제 업무 데이터</span></h2><p>기한 초과 → 오늘 마감 → 높은 우선순위 순입니다.</p></div></div>
        {items.length ? <ol className="priority-list">{items.map((item, index) => <li key={item.task_id}><em>{index + 1}</em><span>{item.title}<small className={`reason ${item.reason}`}>{reasonLabel[item.reason]}{item.due_at ? ` · ${fmtDate(item.due_at)}` : ''}</small></span><button onClick={() => move('tasks', item.reason === 'overdue' ? 'overdue' : 'active')} aria-label="업무 보기">→</button></li>)}</ol> : <div className="empty">지금 우선 확인할 업무가 없습니다.</div>}
      </article>
      <article className="panel mail-panel"><div className="panel-head"><div><h2>메일 <MailBadge state={data.mail.state} /></h2><p>미확인 메일 건수를 확인합니다.</p></div><button className="text-button" onClick={() => move('mail')}>메일 열기</button></div>
        {data.mail.state === 'connected' || data.mail.state === 'mock' ? <button className="mail-count" onClick={() => move('mail')}><b>{data.mail.unread_count ?? 0}</b><span>미확인 메일{data.mail.state === 'mock' ? ' (Mock 데이터)' : ''}</span></button>
          : <div className="empty calendar-state">{data.mail.state === 'session_expired' ? '메일 인증이 필요합니다.' : data.mail.state === 'error' ? '메일을 불러오지 못했습니다.' : '메일이 아직 연결되지 않았습니다.'}<small>{data.mail.detail}</small></div>}
      </article>
      <ProviderPanel providers={data.providers} />
    </section>
  </>
}

function ProviderPanel({ providers }: { providers: Dashboard['providers'] }) { return <article className="panel provider-panel"><div className="panel-head"><div><h2>시스템 상태</h2><p>연결 상태를 한눈에 확인합니다.</p></div></div>{providers.map((provider) => <div className="provider-row" key={provider.name}><span><StatusDot state={provider.state} />{provider.name}</span><small>{provider.detail}</small></div>)}</article> }

function Settings({ refresh }: { refresh: () => Promise<void> }) {
  const [status, setStatus] = useState<CalendarConnectionStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState('')
  useEffect(() => { void api.calendarStatus().then(setStatus).catch(() => setNotice('연결 상태를 확인하지 못했습니다.')) }, [])
  async function run(action: 'connect' | 'verify' | 'disconnect') {
    setLoading(true); setNotice('')
    try {
      const result = action === 'connect' ? await api.connectCalendar() : action === 'verify' ? await api.verifyCalendar() : await api.disconnectCalendar()
      setStatus(result)
      if (action === 'connect') window.workMate?.focusApp?.()
      setNotice(action === 'connect' ? 'Google Calendar 연결을 완료했습니다.' : action === 'verify' ? '연결을 확인했습니다.' : '이 기기의 연결 토큰을 제거했습니다.')
      await refresh()
    } catch (error) { setNotice(error instanceof Error ? error.message : '요청을 처리하지 못했습니다.') } finally { setLoading(false) }
  }
  const connected = status?.state === 'connected'
  return <><PageHeader title="설정" text="Google Calendar 연결과 권한 상태를 관리합니다." /><section className="settings-layout"><article className="panel settings-panel"><div className="panel-head"><div><h2>Google Calendar</h2><p>일정 조회와 승인된 일정 변경에 사용됩니다.</p></div><StatusDot state={status?.state ?? 'offline'} /></div><div className="connection-status"><b>{connected ? '연결됨' : status?.state === 'mock' ? 'Mock 모드' : '연결되지 않음'}</b><small>{status?.detail ?? '상태를 불러오는 중입니다.'}</small><small>Calendar ID: {status?.calendar_id ?? 'primary'}</small></div><div className="action-buttons settings-actions">{connected ? <><button className="secondary" disabled={loading} onClick={() => void run('verify')}>연결 확인</button><button className="primary danger" disabled={loading} onClick={() => void run('disconnect')}>연결 해제</button></> : <button className="primary" disabled={loading || status?.provider === 'mock'} onClick={() => void run('connect')}>{loading ? '브라우저 인증 대기 중…' : 'Google 계정 연결'}</button>}</div>{status?.provider === 'mock' && <p className="settings-hint">`.env`에서 `WORKMATE_CALENDAR_PROVIDER=google`으로 바꾸고 OAuth Desktop Client JSON 경로를 설정하면 실제 계정을 연결할 수 있습니다.</p>}{notice && <p className="settings-notice">{notice}</p>}</article></section></>
}

function Activity({ activities }: { activities: Dashboard['activities'] }) { return <><PageHeader title="활동 기록" text="workMate가 확인하거나 준비한 업무 흐름입니다." /><article className="panel activity-panel"><div className="panel-head"><div><h2>오늘</h2><p>민감한 메일 본문과 인증 정보는 기록하지 않습니다.</p></div></div>{activities.length === 0 && <div className="empty">아직 기록이 없습니다. 업무·일정·메모를 처리하면 이곳에 표시됩니다.</div>}{activities.map((activity) => <div className="activity-row" key={activity.id}><time>{fmtTime(activity.occurred_at)}</time><span className={`activity-icon ${activity.category}`}>{activity.category === 'read' ? '◉' : '✦'}</span><div><b>{activity.action}</b><p>{activity.detail}</p></div></div>)}</article></> }

function Offline({ retry }: { retry: () => void }) { return <main className="offline"><Logo /><h1>Backend Offline</h1><p>workMate 백엔드에 연결할 수 없습니다. FastAPI 서버가 실행 중인지 확인한 뒤 다시 시도해 주세요.</p><button className="primary" onClick={retry}>다시 연결</button><code>cd backend && python3 -m uvicorn server.main:app --port 8765</code></main> }

const CONVERSATION_KEY = 'workmate.assistant.conversation'
// undefined = never chosen (reopen the latest), null = an explicit empty "new conversation"
function readConversation(): string | null | undefined { try { const value = window.localStorage.getItem(CONVERSATION_KEY); return value === null ? undefined : value === '' ? null : value } catch { return undefined } }

export default function App() {
  const [page, setPage] = useState<Page>('home'); const [taskView, setTaskView] = useState<TaskView>('active'); const [conversationId, setConversationState] = useState<string | null | undefined>(readConversation); const [draft, setDraft] = useState(''); const [data, setData] = useState<Dashboard | null>(null); const [offline, setOffline] = useState(false)
  const setConversationId = useCallback((id: string | null) => { setConversationState(id); try { window.localStorage.setItem(CONVERSATION_KEY, id ?? '') } catch { /* private mode: keep it in memory only */ } }, [])
  const load = async () => { try { await api.health(); setData(await api.dashboard()); setOffline(false) } catch { setOffline(true) } }
  useEffect(() => {
    // Initial network synchronization intentionally begins after the renderer mounts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    window.workMate?.onNavigate((next) => setPage(next as Page))
  }, [])
  const content = useMemo(() => { if (!data) return null; if (page === 'assistant') return <Assistant data={data} refresh={load} openMail={() => setPage('mail')} conversationId={conversationId} setConversationId={setConversationId} draft={draft} setDraft={setDraft} />; if (page === 'tasks') return <Tasks summary={data.briefing.task_summary} initialView={taskView} refresh={load} />; if (page === 'memos') return <Memos />; if (page === 'mail') return <Mail refresh={load} />; if (page === 'knowledge') return <KnowledgeBase />; if (page === 'activity') return <Activity activities={data.activities} />; if (page === 'settings') return <Settings refresh={load} />; return <Home data={data} move={(next, view) => { if (view) setTaskView(view); setPage(next) }} /> }, [data, page, taskView, conversationId, draft, setConversationId])
  if (offline) return <Offline retry={() => void load()} />
  if (!data) return <div className="loading"><Logo /><span>workMate를 준비하는 중…</span></div>
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><Logo /><div><b>workMate</b><small>개인 AI 업무 비서</small></div></div><nav>{nav.map((item) => <button className={page === item.id ? 'active' : ''} onClick={() => setPage(item.id)} key={item.id}><span>{item.icon}</span>{item.label}</button>)}</nav><div className="sidebar-bottom"><div className="privacy"><span>⌘</span><p><b>안전한 업무 처리</b><small>변경 작업은 항상 확인 후 실행</small></p></div><button className={`settings ${page === 'settings' ? 'active' : ''}`} onClick={() => setPage('settings')}>⚙ 설정 <span>›</span></button></div></aside><main className="main-content">{content}</main></div>
}

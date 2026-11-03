import type { ActionCard } from './types'

const approveLabel: Partial<Record<ActionCard['kind'], string>> = { calendar_delete: '삭제', mail_send: '발송', mail_delete: '삭제', mail_move: '이동', mail_read: '변경', task_delete: '삭제', memo_delete: '삭제', task_complete: '완료 처리', task_update: '수정', calendar_update: '수정', memo_update: '수정', task_create: '추가', memo_create: '저장' }
const statusLabel: Partial<Record<ActionCard['status'], string>> = { completed: '완료됨', failed: '실패', rejected: '취소됨', executing: '실행 중…', expired: '만료됨', needs_check: '확인 필요' }

export function ActionPreview({ action, onApprove, onReject, busy }: { action: ActionCard; onApprove: (action: ActionCard) => void; onReject: (action: ActionCard) => void; busy: boolean }) {
  const executable = !!action.action_id
  const pending = action.status === 'pending_approval'
  const expired = action.status === 'expired'
  const needsCheck = action.status === 'needs_check'
  const label = approveLabel[action.kind] ?? '등록'
  const destructive = action.kind.endsWith('_delete')
  return <div className={`action-card ${expired ? 'expired' : ''}`}><div className="action-heading"><span>{action.kind === 'mail_draft' ? '✉' : '▣'}</span><div><b>{action.title}</b><small>{pending ? '승인 전에는 아무것도 변경되지 않습니다.' : `상태: ${statusLabel[action.status] ?? label}`}</small></div></div>{Object.entries(action.fields).map(([field, value]) => <div className="action-field" key={field}><small>{field}</small><p>{value}</p></div>)}
    {needsCheck ? <p className="expired-note warn">발송 결과를 알 수 없습니다. 보낸편지함에서 확인하세요. 자동으로 다시 보내지 않습니다.</p> : expired ? <p className="expired-note">앱을 다시 시작해 이 카드는 더 이상 승인할 수 없습니다. 필요하면 다시 요청해 주세요.</p>
      : <div className="action-buttons"><button className="secondary" disabled={!executable || !pending || busy} onClick={() => onReject(action)}>취소</button><button className={`primary ${destructive ? 'danger' : ''}`} disabled={!executable || !pending || busy} onClick={() => executable && onApprove(action)}>{busy ? '실행 중…' : executable ? label : '검토 후 보내기'}</button></div>}</div>
}


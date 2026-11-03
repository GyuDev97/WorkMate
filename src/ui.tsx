import { useEffect, type ReactNode } from 'react'

export function Logo() { return <div className="brand-mark"><span>✦</span></div> }
export function StatusDot({ state }: { state: string }) { return <span className={`status-dot ${state}`} /> }
export function Empty({ text }: { text: string }) { return <div className="empty">{text}</div> }
export function ErrorNote({ text }: { text: string }) { return text ? <p className="error-note" role="alert">{text}</p> : null }
export function PageHeader({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <header className="page-header"><div><p className="eyebrow">WORKMATE</p><h1>{title}</h1><p>{text}</p></div>{action}</header>
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="panel-head"><h2>{title}</h2><button className="text-button" onClick={onClose} aria-label="닫기">✕</button></div>{children}</div>
  </div>
}

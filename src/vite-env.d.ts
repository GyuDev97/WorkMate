/// <reference types="vite/client" />

interface Window {
  workMate?: {
    platform: string
    focusApp?: () => void
    onNavigate: (callback: (page: string) => void) => void
  }
}

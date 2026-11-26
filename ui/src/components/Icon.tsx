import {
  Activity, ArrowLeft, ArrowUpToLine, ChartColumn, TriangleAlert, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleHelp, CircleX, Clock, Copy, Cpu, Download, EllipsisVertical, Eye, Files,
  Film, Folder, FolderInput, FolderOpen, GitBranch, LayoutDashboard, Lock, LockOpen, LogIn, Pause, Pencil, Play, Plus, RotateCcw, Rocket, Save, Search, Square,
  ListOrdered, LogOut, MoveRight, Send, Server, Settings, TextCursorInput, Timer, Trash2, Undo2, Upload, Users, Workflow, X, type LucideIcon,
} from 'lucide-react'

const icons: Record<string, LucideIcon> = {
  activity: Activity, alert: TriangleAlert, 'arrow-left': ArrowLeft, 'arrow-up': ArrowUpToLine, check: Check, timer: Timer, move: MoveRight,
  rename: TextCursorInput, 'list-ordered': ListOrdered, more: EllipsisVertical, 'chevron-up': ChevronUp, 'chevron-down': ChevronDown, 'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight, help: CircleHelp, cancel: CircleX, clock: Clock, copy: Copy, cpu: Cpu, eye: Eye, files: Files,
  film: Film, folder: Folder, 'folder-open': FolderOpen, 'folder-input': FolderInput, 'git-branch': GitBranch, dashboard: LayoutDashboard, report: ChartColumn, download: Download, lock: Lock, 'lock-open': LockOpen, 'log-in': LogIn,
  pause: Pause, pencil: Pencil, play: Play, stop: Square, plus: Plus, reprocess: RotateCcw, rocket: Rocket, save: Save, search: Search,
  send: Send, server: Server, settings: Settings, trash: Trash2, undo: Undo2, upload: Upload, workflow: Workflow, close: X, users: Users, 'log-out': LogOut,
}

function CatWhiskers({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 5c.67 0 1.35.09 2 .26 1.78-2 5.03-2.84 6.42-2.26 1.4.58-.42 7-.42 7 .57 1.07 1 2.24 1 3.44C21 17.9 16.97 21 12 21s-9-3-9-7.56c0-1.25.5-2.4 1-3.44 0 0-1.89-6.42-.5-7 1.39-.58 4.72.23 6.5 2.23A9.04 9.04 0 0 1 12 5Z" />
      <path d="M8 14v.5" />
      <path d="M16 14v.5" />
      <path d="M11.25 16.25h1.5L12 17l-.75-.75Z" />
      <path d="M7.5 15.5 1.5 14.5" />
      <path d="M7.5 17 2 18.5" />
      <path d="M16.5 15.5 22.5 14.5" />
      <path d="M16.5 17 22 18.5" />
    </svg>
  )
}

export function Icon({ name, size = 15 }: { name: string; size?: number }) {
  if (name === 'cat') return <CatWhiskers size={size} />
  if (name === 'stop-red') return <span className="text-error"><Square size={size} strokeWidth={1.75} aria-hidden /></span>
  const Component = icons[name] ?? CircleHelp
  return <Component size={size} strokeWidth={1.75} aria-hidden />
}

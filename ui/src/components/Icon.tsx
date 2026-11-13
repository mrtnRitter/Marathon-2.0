import {
  Activity, ArrowLeft, ArrowUpToLine, Cat, ChartColumn, Check, ChevronLeft, ChevronRight, CircleHelp, CircleX, Clock, Copy, Cpu, Download, Eye, Files,
  Film, Folder, FolderInput, FolderOpen, GitBranch, LayoutDashboard, Lock, LockOpen, LogIn, Pause, Pencil, Play, Plus, RotateCcw, Rocket, Save, Search,
  Send, Server, Settings, Trash2, Undo2, Upload, Workflow, X, type LucideIcon,
} from 'lucide-react'

const icons: Record<string, LucideIcon> = {
  activity: Activity, 'arrow-left': ArrowLeft, 'arrow-up': ArrowUpToLine, cat: Cat, check: Check, 'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight, help: CircleHelp, cancel: CircleX, clock: Clock, copy: Copy, cpu: Cpu, eye: Eye, files: Files,
  film: Film, folder: Folder, 'folder-open': FolderOpen, 'folder-input': FolderInput, 'git-branch': GitBranch, dashboard: LayoutDashboard, report: ChartColumn, download: Download, lock: Lock, 'lock-open': LockOpen, 'log-in': LogIn,
  pause: Pause, pencil: Pencil, play: Play, plus: Plus, reprocess: RotateCcw, rocket: Rocket, save: Save, search: Search,
  send: Send, server: Server, settings: Settings, trash: Trash2, undo: Undo2, upload: Upload, workflow: Workflow, close: X,
}

export function Icon({ name, size = 15 }: { name: string; size?: number }) {
  const Component = icons[name] ?? CircleHelp
  return <Component size={size} strokeWidth={1.75} aria-hidden />
}

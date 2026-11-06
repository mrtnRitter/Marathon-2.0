import {
  Activity, ArrowLeft, ArrowUpToLine, Check, ChevronLeft, ChevronRight, CircleHelp, CircleX, Clock, Copy, Cpu, Eye, Files,
  Film, FolderInput, GitBranch, LayoutDashboard, Lock, LogIn, Pause, Pencil, Play, Plus, RotateCcw, Rocket, Save, Search,
  Server, Settings, Trash2, Undo2, Upload, Workflow, X, type LucideIcon,
} from 'lucide-react'

const icons: Record<string, LucideIcon> = {
  activity: Activity, 'arrow-left': ArrowLeft, 'arrow-up': ArrowUpToLine, check: Check, 'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight, help: CircleHelp, cancel: CircleX, clock: Clock, copy: Copy, cpu: Cpu, eye: Eye, files: Files,
  film: Film, 'folder-input': FolderInput, 'git-branch': GitBranch, dashboard: LayoutDashboard, lock: Lock, 'log-in': LogIn,
  pause: Pause, pencil: Pencil, play: Play, plus: Plus, reprocess: RotateCcw, rocket: Rocket, save: Save, search: Search,
  server: Server, settings: Settings, trash: Trash2, undo: Undo2, upload: Upload, workflow: Workflow, close: X,
}

export function Icon({ name, size = 15 }: { name: string; size?: number }) {
  const Component = icons[name] ?? CircleHelp
  return <Component size={size} strokeWidth={1.75} aria-hidden />
}

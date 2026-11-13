import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, list_dirs } from '../data/api'
import type { DirListing } from '../data/types'
import { Icon } from './Icon'
import { SlidePanel } from './SlidePanel'
import './FolderDialog.css'

interface Props {
  open: boolean
  initial_path: string
  on_select: (path: string) => void
  on_close: () => void
}

/** Browse server folders and select a path. */
export function FolderDialog({ open, initial_path, on_select, on_close }: Props) {
  const request_number = useRef(0)
  const [listing, set_listing] = useState<DirListing | null>(null)
  const [path_input, set_path_input] = useState('')
  const [loading, set_loading] = useState(false)
  const [error, set_error] = useState<string | null>(null)

  const load_dirs = useCallback(async (path: string | null, preserve_error = false, fallback_to_roots = false): Promise<void> => {
    const current_request = ++request_number.current
    set_listing(null)
    set_loading(true)
    if (!preserve_error) set_error(null)
    try {
      const result = await list_dirs(path)
      if (current_request !== request_number.current) return
      set_listing(result)
      set_path_input(result.path ?? '')
    } catch (caught) {
      if (current_request !== request_number.current) return
      set_error(caught instanceof ApiError ? caught.detail : 'Server nicht erreichbar')
      if (fallback_to_roots) {
        const roots_request = ++request_number.current
        try {
          const roots = await list_dirs()
          if (roots_request !== request_number.current) return
          set_listing(roots)
          set_path_input('')
        } catch (root_error) {
          if (roots_request !== request_number.current) return
          set_error(root_error instanceof ApiError ? root_error.detail : 'Server nicht erreichbar')
        } finally {
          if (roots_request === request_number.current) set_loading(false)
        }
        return
      }
    } finally {
      if (current_request === request_number.current) set_loading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    const path = initial_path.trim()
    set_listing(null)
    set_path_input(path)
    void load_dirs(path || null, false, Boolean(path))
    return () => { request_number.current += 1 }
  }, [open, initial_path, load_dirs])

  const actions = [
    { label: 'Abbrechen', icon: 'close', on_click: on_close },
    {
      label: 'Diesen Ordner wählen',
      icon: 'folder-open',
      on_click: () => {
        if (!listing || listing.path === null) return
        on_select(listing.path)
        on_close()
      },
      disabled: !listing || listing.path === null,
      primary: true,
    },
  ]

  return (
    <SlidePanel open={open} title="Ordner wählen" icon="folder-open" on_close={on_close} actions={actions}>
      <div className="folder-dialog-navigation">
        <input
          className="input folder-dialog-path"
          aria-label="Pfad"
          value={path_input}
          onChange={event => set_path_input(event.target.value)}
          onKeyDown={event => {
            if (event.key !== 'Enter') return
            event.preventDefault()
            void load_dirs(path_input.trim() || null)
          }}
        />
        <button type="button" className="button" onClick={() => void load_dirs(listing?.parent ?? null)} disabled={!listing || loading}>
          <Icon name="arrow-up" size={14} /><span>Nach oben</span>
        </button>
        <button type="button" className="button" onClick={() => void load_dirs(null)} disabled={loading}>
          <Icon name="server" size={14} /><span>Laufwerke</span>
        </button>
      </div>
      {loading && <div className="folder-dialog-loading" role="status">Lade …</div>}
      {error && <div className="form-field-error" role="alert">{error}</div>}
      {listing?.truncated && <div className="folder-dialog-truncated">Nur die ersten 1000 Ordner werden angezeigt</div>}
      <ul className="folder-dialog-list">
        {listing?.dirs.map(dir => (
          <li key={dir.path}>
            <button type="button" className="folder-dialog-entry" onClick={() => void load_dirs(dir.path)} disabled={loading}>
              <Icon name="folder" size={16} /><span>{dir.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </SlidePanel>
  )
}

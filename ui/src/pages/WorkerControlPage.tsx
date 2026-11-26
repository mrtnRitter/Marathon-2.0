import { useEffect, useRef, useState } from 'react'
import { AvailabilityEditor } from '../components/AvailabilityEditor'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/StatusBadge'
import { WorkerControlError, exchange_worker_control_token, get_worker_control_self, start_worker_control, stop_drain_worker_control, stop_now_worker_control, update_worker_control_availability, type WorkerControlView } from '../data/worker_control'
import type { AvailabilityConfig } from '../data/types'
import { availability_text, can_start, can_stop_drain, can_stop_now } from '../lib/worker_actions'
import { worker_approval_meta, worker_status_meta } from '../theme/status'
import './WorkerControlPage.css'

const refresh_interval_ms = 5_000

type Action = 'start' | 'stop_drain' | 'stop_now' | 'availability'

function error_detail(error: unknown): string {
  return error instanceof WorkerControlError ? error.detail : error instanceof Error ? error.message : 'Anfrage fehlgeschlagen'
}

export function WorkerControlPage() {
  const [worker, set_worker] = useState<WorkerControlView | null>(null)
  const [availability, set_availability] = useState<AvailabilityConfig | null>(null)
  const [loading, set_loading] = useState(true)
  const [busy, set_busy] = useState<Action | null>(null)
  const [error, set_error] = useState<string | null>(null)
  const [saved, set_saved] = useState(false)
  const initial_request = useRef<Promise<WorkerControlView> | null>(null)
  const request_controllers = useRef(new Set<AbortController>())
  const mounted = useRef(false)
  const dirty = useRef(false)
  const initial_loaded = useRef(false)
  const initial_succeeded = useRef(false)
  const refresh_in_flight = useRef(false)
  const mutation_in_flight = useRef(false)
  const mutation_generation = useRef(0)

  useEffect(() => {
    let active = true
    mounted.current = true
    if (!initial_request.current) {
      const params = new URLSearchParams(window.location.search)
      const token = params.get('token')
      const controller = new AbortController()
      request_controllers.current.add(controller)
      const request = token !== null
        ? exchange_worker_control_token(token, controller.signal)
        : get_worker_control_self(controller.signal)
      initial_request.current = request.finally(() => {
        request_controllers.current.delete(controller)
        if (token !== null) window.history.replaceState(window.history.state, '', '/worker-control')
      })
    }
    void initial_request.current
      .then(result => {
        if (!active) return
        set_worker(result)
        set_availability(result.availability_config)
        dirty.current = false
        initial_succeeded.current = true
        set_error(null)
      })
      .catch(reason => {
        if (active) set_error(error_detail(reason))
      })
      .finally(() => {
        initial_loaded.current = true
        if (active) set_loading(false)
      })

    const refresh = async () => {
      if (!active || !initial_loaded.current || !initial_succeeded.current || document.visibilityState !== 'visible' || refresh_in_flight.current || mutation_in_flight.current) return
      refresh_in_flight.current = true
      const generation = mutation_generation.current
      const controller = new AbortController()
      request_controllers.current.add(controller)
      try {
        const result = await get_worker_control_self(controller.signal)
        if (!active || generation !== mutation_generation.current) return
        set_worker(result)
        if (!dirty.current) set_availability(result.availability_config)
        set_error(null)
      } catch (reason) {
        if (!active || generation !== mutation_generation.current) return
        if (reason instanceof WorkerControlError && reason.status === 401) {
          set_worker(null)
          set_availability(null)
          initial_succeeded.current = false
        }
        set_error(error_detail(reason))
      } finally {
        request_controllers.current.delete(controller)
        refresh_in_flight.current = false
      }
    }
    const on_visibility_change = () => { if (document.visibilityState === 'visible') void refresh() }
    const timer = window.setInterval(() => { void refresh() }, refresh_interval_ms)
    document.addEventListener('visibilitychange', on_visibility_change)
    return () => {
      active = false
      mounted.current = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', on_visibility_change)
      queueMicrotask(() => {
        if (mounted.current) return
        for (const controller of request_controllers.current) controller.abort()
        request_controllers.current.clear()
      })
    }
  }, [])

  const run_action = async (action: Exclude<Action, 'availability'>) => {
    if (!worker || busy || mutation_in_flight.current) return
    mutation_in_flight.current = true
    set_busy(action)
    set_error(null)
    const generation = ++mutation_generation.current
    const controller = new AbortController()
    request_controllers.current.add(controller)
    try {
      const result = action === 'start'
        ? await start_worker_control(controller.signal)
        : action === 'stop_drain'
          ? await stop_drain_worker_control(controller.signal)
          : await stop_now_worker_control(controller.signal)
      if (!mounted.current || generation !== mutation_generation.current) return
      set_worker(result)
      if (!dirty.current) set_availability(result.availability_config)
    } catch (reason) {
      if (mounted.current && generation === mutation_generation.current) {
        if (reason instanceof WorkerControlError && reason.status === 401) {
          set_worker(null)
          initial_succeeded.current = false
        }
        set_error(error_detail(reason))
      }
    } finally {
      request_controllers.current.delete(controller)
      mutation_in_flight.current = false
      if (mounted.current) set_busy(null)
    }
  }

  const save_availability = async () => {
    if (!availability || busy || mutation_in_flight.current) return
    mutation_in_flight.current = true
    set_busy('availability')
    set_error(null)
    set_saved(false)
    const generation = ++mutation_generation.current
    const controller = new AbortController()
    request_controllers.current.add(controller)
    try {
      const result = await update_worker_control_availability(availability, controller.signal)
      if (!mounted.current || generation !== mutation_generation.current) return
      set_worker(result)
      set_availability(result.availability_config)
      dirty.current = false
      set_saved(true)
    } catch (reason) {
      if (mounted.current && generation === mutation_generation.current) {
        if (reason instanceof WorkerControlError && reason.status === 401) {
          set_worker(null)
          initial_succeeded.current = false
        }
        set_error(error_detail(reason))
      }
    } finally {
      request_controllers.current.delete(controller)
      mutation_in_flight.current = false
      if (mounted.current) set_busy(null)
    }
  }

  if (loading && !worker) {
    return <main className="worker-control-page"><div className="worker-control-message" role="status">Lade</div></main>
  }
  if (!worker) {
    return <main className="worker-control-page"><div className="worker-control-message" role="alert">{error || 'Worker nicht verfügbar'}</div></main>
  }

  return (
    <main className="worker-control-page">
      <div className="worker-control-content">
        <h1>Worker-Einstellungen</h1>
        {error && <div className="worker-control-error" role="alert">{error}</div>}
        <section className="worker-control-card">
          <div className="worker-control-heading">
            <h2>{worker.name}</h2>
            <div className="worker-control-badges">
              <StatusBadge meta={worker_status_meta[worker.status]} />
              {!worker.approved && <StatusBadge meta={worker_approval_meta} />}
            </div>
          </div>
          {!worker.approved && <p className="worker-control-pending">Dieser Worker wartet auf Freigabe durch einen Administrator</p>}
          <div className="worker-control-availability"><span>Verfügbar</span><strong>{availability_text(worker)}</strong></div>
          <div className="worker-control-actions">
            <button type="button" className="button" disabled={Boolean(busy) || !can_start(worker)} onClick={() => { void run_action('start') }}><Icon name="play" size={14} /><span>Starten</span></button>
            <button type="button" className="button worker-control-stop" disabled={Boolean(busy) || !can_stop_drain(worker)} onClick={() => { void run_action('stop_drain') }}><Icon name="stop" size={14} /><span>Stoppen</span></button>
            <button type="button" className="button" disabled={Boolean(busy) || !can_stop_now(worker)} onClick={() => { void run_action('stop_now') }}><Icon name="stop-red" size={14} /><span>Sofort-Aus</span></button>
          </div>
        </section>

        <section className="worker-control-card">
          <h2>Fähigkeiten</h2>
          <div>{worker.skill_names.length ? worker.skill_names.map(skill => <span className="chip" key={skill}>{skill}</span>) : <span className="dim">–</span>}</div>
          <p className="worker-control-hint">Fähigkeiten legt ein Administrator fest.</p>
        </section>

        <section className="worker-control-card">
          <h2>Verfügbarkeit</h2>
          {availability && <AvailabilityEditor value={availability} disabled={busy === 'availability'} on_change={next => {
            dirty.current = JSON.stringify(next) !== JSON.stringify(worker.availability_config)
            set_saved(false)
            set_availability(next)
          }} />}
          <div className="worker-control-save">
            {saved && <span role="status">Gespeichert</span>}
            <button type="button" className="button button-primary" disabled={Boolean(busy) || !availability} onClick={() => { void save_availability() }}><Icon name="save" size={14} /><span>Speichern</span></button>
          </div>
        </section>
      </div>
    </main>
  )
}

import * as React from "react"
import { useLocation } from "react-router"

import { getAlerts, investigate as investigateApi } from "@/api/alerts"
import type { Case, InvestigateEvent, QueueEntry, QueueLabel } from "@/api/types"
import { playAlertSound } from "@/lib/alert-sound"

type ConnectionStatus = "connecting" | "ready"

type InvestigationState = {
  events: InvestigateEvent[]
  status: "streaming" | "done" | "error"
}

type AlertsContextValue = {
  alerts: QueueEntry[]
  status: ConnectionStatus
  /** Post-hoc ground truth revealed by a fresh investigate call, session-only (BACKEND.md §4.1) — never persisted. */
  truthByAlertId: Record<string, boolean | null>
  /** Live investigate streams keyed by alert_id — the single source of truth so the queue row and the case drawer never start two concurrent (and separately billed) investigations for the same alert. */
  investigations: Record<string, InvestigationState>
  refresh: () => Promise<void>
  applyCase: (alertId: string, updatedCase: Case) => void
  applyLabel: (alertId: string, label: QueueLabel) => void
  applyTruth: (alertId: string, real: boolean | null) => void
  /** Starts investigating an alert unless it's already running/started. */
  ensureInvestigating: (alertId: string) => void
  /** Forces a fresh investigate stream even if one already ran (used after failures). */
  retryInvestigating: (alertId: string) => void
}

const AlertsContext = React.createContext<AlertsContextValue | undefined>(undefined)

const RETRY_DELAY_MS = 3000

/**
 * The backend refuses connections while loading the graph, and live NFC alerts can arrive
 * afterward, so keep polling GET /api/alerts instead of treating the queue as a snapshot.
 */
export function AlertsProvider({ children }: { children: React.ReactNode }) {
  const [alerts, setAlerts] = React.useState<QueueEntry[]>([])
  const [status, setStatus] = React.useState<ConnectionStatus>("connecting")
  const [truthByAlertId, setTruthByAlertId] = React.useState<Record<string, boolean | null>>({})
  const [investigations, setInvestigations] = React.useState<Record<string, InvestigationState>>(
    {}
  )
  const startedAlertIds = React.useRef<Set<string>>(new Set())
  /** Alert ids seen on the previous poll; null until the first successful fetch so the
   * existing queue never triggers a sound on initial load — only alerts that arrive after. */
  const knownAlertIds = React.useRef<Set<string> | null>(null)
  /** Only chime while the user is actually looking at a cases queue, not on Home/Settings/etc. */
  const pathname = useLocation().pathname
  const pathnameRef = React.useRef(pathname)
  React.useEffect(() => {
    pathnameRef.current = pathname
  }, [pathname])

  const applyAlerts = React.useCallback((data: QueueEntry[]) => {
    const previouslyKnown = knownAlertIds.current
    const hasNewAlert = previouslyKnown && data.some((entry) => !previouslyKnown.has(entry.alert_id))
    if (hasNewAlert && pathnameRef.current.startsWith("/cases")) {
      playAlertSound()
    }
    knownAlertIds.current = new Set(data.map((entry) => entry.alert_id))
    setAlerts(data)
  }, [])

  const refresh = React.useCallback(async () => {
    const data = await getAlerts()
    applyAlerts(data)
    setStatus("ready")
  }, [applyAlerts])

  React.useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const attempt = () => {
      getAlerts()
        .then((data) => {
          if (cancelled) return
          applyAlerts(data)
          setStatus("ready")
        })
        .catch(() => undefined)
        .finally(() => {
          if (!cancelled) timer = setTimeout(attempt, RETRY_DELAY_MS)
        })
    }

    attempt()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [applyAlerts])

  const applyCase = React.useCallback((alertId: string, updatedCase: Case) => {
    setAlerts((prev) =>
      prev.map((entry) => (entry.alert_id === alertId ? { ...entry, case: updatedCase } : entry))
    )
  }, [])

  const applyLabel = React.useCallback((alertId: string, label: QueueLabel) => {
    setAlerts((prev) =>
      prev.map((entry) => (entry.alert_id === alertId ? { ...entry, label } : entry))
    )
  }, [])

  const applyTruth = React.useCallback((alertId: string, real: boolean | null) => {
    setTruthByAlertId((prev) => ({ ...prev, [alertId]: real }))
  }, [])

  const beginInvestigate = React.useCallback(
    (alertId: string) => {
      startedAlertIds.current.add(alertId)
      setInvestigations((prev) => ({ ...prev, [alertId]: { events: [], status: "streaming" } }))
      investigateApi(alertId, (event) => {
        setInvestigations((prev) => {
          const events = [...(prev[alertId]?.events ?? []), event]
          const nextStatus =
            event.type === "done" ? "done" : event.type === "error" ? "error" : "streaming"
          return { ...prev, [alertId]: { events, status: nextStatus } }
        })
        if (event.type === "done") {
          applyCase(alertId, event.case)
          applyTruth(alertId, event.truth.real)
        }
      })
    },
    [applyCase, applyTruth]
  )

  const ensureInvestigating = React.useCallback(
    (alertId: string) => {
      if (startedAlertIds.current.has(alertId)) return
      beginInvestigate(alertId)
    },
    [beginInvestigate]
  )

  const retryInvestigating = React.useCallback(
    (alertId: string) => {
      beginInvestigate(alertId)
    },
    [beginInvestigate]
  )

  const value = React.useMemo(
    () => ({
      alerts,
      status,
      truthByAlertId,
      investigations,
      refresh,
      applyCase,
      applyLabel,
      applyTruth,
      ensureInvestigating,
      retryInvestigating,
    }),
    [
      alerts,
      status,
      truthByAlertId,
      investigations,
      refresh,
      applyCase,
      applyLabel,
      applyTruth,
      ensureInvestigating,
      retryInvestigating,
    ]
  )

  return <AlertsContext.Provider value={value}>{children}</AlertsContext.Provider>
}

export function useAlerts(): AlertsContextValue {
  const context = React.useContext(AlertsContext)
  if (context === undefined) {
    throw new Error("useAlerts must be used within an AlertsProvider")
  }
  return context
}

import * as React from "react"

import { getAlerts, investigate as investigateApi } from "@/api/alerts"
import type { Case, InvestigateEvent, QueueEntry, QueueLabel } from "@/api/types"

type ConnectionStatus = "connecting" | "ready"

type InvestigationState = {
  events: InvestigateEvent[]
  status: "streaming" | "done" | "error"
}

type AlertsContextValue = {
  alerts: QueueEntry[]
  status: ConnectionStatus
  /** Post-hoc ground truth revealed by a fresh investigate call, session-only (BACKEND.md §4.1) — never persisted. */
  truthByAlertId: Record<string, boolean>
  /** Live investigate streams keyed by alert_id — the single source of truth so the queue row and the case drawer never start two concurrent (and separately billed) investigations for the same alert. */
  investigations: Record<string, InvestigationState>
  refresh: () => Promise<void>
  applyCase: (alertId: string, updatedCase: Case) => void
  applyLabel: (alertId: string, label: QueueLabel) => void
  applyTruth: (alertId: string, real: boolean) => void
  /** Starts investigating an alert unless it's already running/started. */
  ensureInvestigating: (alertId: string) => void
  /** Forces a fresh investigate stream even if one already ran (used after failures). */
  retryInvestigating: (alertId: string) => void
}

const AlertsContext = React.createContext<AlertsContextValue | undefined>(undefined)

const RETRY_DELAY_MS = 3000

/**
 * The backend refuses connections for ~30-60s while it loads the transaction graph
 * (BACKEND.md §1) — poll GET /api/alerts until the first successful response instead of
 * surfacing a hard error.
 */
export function AlertsProvider({ children }: { children: React.ReactNode }) {
  const [alerts, setAlerts] = React.useState<QueueEntry[]>([])
  const [status, setStatus] = React.useState<ConnectionStatus>("connecting")
  const [truthByAlertId, setTruthByAlertId] = React.useState<Record<string, boolean>>({})
  const [investigations, setInvestigations] = React.useState<Record<string, InvestigationState>>(
    {}
  )
  const startedAlertIds = React.useRef<Set<string>>(new Set())

  const refresh = React.useCallback(async () => {
    const data = await getAlerts()
    setAlerts(data)
    setStatus("ready")
  }, [])

  React.useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const attempt = () => {
      getAlerts()
        .then((data) => {
          if (cancelled) return
          setAlerts(data)
          setStatus("ready")
        })
        .catch(() => {
          if (cancelled) return
          timer = setTimeout(attempt, RETRY_DELAY_MS)
        })
    }

    attempt()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

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

  const applyTruth = React.useCallback((alertId: string, real: boolean) => {
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

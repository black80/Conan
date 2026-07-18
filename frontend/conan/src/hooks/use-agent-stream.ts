import * as React from "react"

export type StreamStatus = "idle" | "streaming" | "done" | "error"

type Unsubscribe = () => void
type Runner<TEvent> = (
  onEvent: (event: TEvent) => void,
  signal: AbortSignal
) => void | Promise<void> | Unsubscribe

/**
 * Drives either SSE transport (EventSource for GET /api/investigate, or the fetch+reader
 * sseFetch pattern for POST streams) into a uniform events/status pair, so any page can
 * start a stream without re-implementing subscription/cleanup bookkeeping.
 */
export function useAgentStream<TEvent extends { type: string }>() {
  const [events, setEvents] = React.useState<TEvent[]>([])
  const [status, setStatus] = React.useState<StreamStatus>("idle")
  const cleanupRef = React.useRef<Unsubscribe | null>(null)

  const reset = React.useCallback(() => {
    cleanupRef.current?.()
    cleanupRef.current = null
    setEvents([])
    setStatus("idle")
  }, [])

  const start = React.useCallback((run: Runner<TEvent>) => {
    cleanupRef.current?.()
    setEvents([])
    setStatus("streaming")

    const controller = new AbortController()

    const onEvent = (event: TEvent) => {
      setEvents((prev) => [...prev, event])
      if (event.type === "done") setStatus("done")
      if (event.type === "error") setStatus("error")
    }

    const result = run(onEvent, controller.signal)

    if (typeof result === "function") {
      cleanupRef.current = result
    } else {
      cleanupRef.current = () => controller.abort()
      result?.catch(() => setStatus("error"))
    }
  }, [])

  React.useEffect(() => () => cleanupRef.current?.(), [])

  return { events, status, start, reset }
}

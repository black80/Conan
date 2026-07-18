import { apiFetch } from "@/api/client"
import { streamEventSource } from "@/api/sse"
import type { InvestigateEvent, QueueEntry } from "@/api/types"

export function getAlerts(): Promise<QueueEntry[]> {
  return apiFetch<QueueEntry[]>("/api/alerts")
}

/** Runs one real investigation over SSE; returns an unsubscribe function. */
export function investigate(alertId: string, onEvent: (event: InvestigateEvent) => void): () => void {
  return streamEventSource<InvestigateEvent>("/api/investigate", { id: alertId }, onEvent)
}

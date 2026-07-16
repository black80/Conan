import { apiFetch } from "@/api/client"
import { sseFetch } from "@/api/sse"
import type { FailureRow, RerunEvent, ReviewEvent, ReviewResult } from "@/api/types"

export function getFailures(): Promise<FailureRow[]> {
  return apiFetch<FailureRow[]>("/api/tuning/failures")
}

export function reviewFailures(
  onEvent: (event: ReviewEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return sseFetch<ReviewEvent>("/api/tuning/review", {}, onEvent, signal)
}

export function approveReview(
  review: Pick<ReviewResult, "cards" | "error_patterns" | "summary">
): Promise<{ version: number; n_cards: number }> {
  return apiFetch("/api/tuning/approve", {
    method: "POST",
    body: JSON.stringify(review),
  })
}

export function rerunCase(
  caseId: string,
  onEvent: (event: RerunEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return sseFetch<RerunEvent>(`/api/tuning/rerun/${caseId}`, {}, onEvent, signal)
}

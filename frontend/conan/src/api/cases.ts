import { apiFetch } from "@/api/client"
import { sseFetch } from "@/api/sse"
import type { AskEvent, ChatTurn, HumanLabel, LabelRecord } from "@/api/types"

export function labelCase(
  caseId: string,
  label: HumanLabel,
  note?: string
): Promise<LabelRecord> {
  return apiFetch<LabelRecord>(`/api/cases/${caseId}/label`, {
    method: "POST",
    body: JSON.stringify({ label, note }),
  })
}

export function askCase(
  caseId: string,
  question: string,
  history: ChatTurn[],
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return sseFetch<AskEvent>(`/api/cases/${caseId}/ask`, { question, history }, onEvent, signal)
}

import { apiUrl } from "@/api/client"
import { USE_MOCKS } from "@/api/mocks/flag"
import { mockEventSource, mockSseFetch } from "@/api/mocks/stream"

/**
 * GET /api/investigate is EventSource-compatible (BACKEND.md §2). The POST streaming
 * endpoints (ask/review/rerun/propose) are not — EventSource is GET-only — so they use
 * sseFetch below instead.
 */
export function streamEventSource<TEvent extends { type: string }>(
  path: string,
  params: Record<string, string>,
  onEvent: (event: TEvent) => void
): () => void {
  if (USE_MOCKS) return mockEventSource<TEvent>(path, params, onEvent)

  const url = new URL(apiUrl(path))
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value)
  }

  const source = new EventSource(url)

  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as TEvent
    onEvent(event)
    if (event.type === "done" || event.type === "error") {
      source.close()
    }
  }

  source.onerror = () => {
    source.close()
  }

  return () => source.close()
}

/**
 * Fetch + manual stream reading for POST SSE endpoints, per the sseFetch pattern
 * documented in BACKEND.md §2 (EventSource can't do POST bodies).
 */
export async function sseFetch<TEvent extends { type: string }>(
  path: string,
  body: unknown,
  onEvent: (event: TEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  if (USE_MOCKS) return mockSseFetch<TEvent>(path, body, onEvent, signal)

  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  })

  if (!res.ok || !res.body) {
    const message = (await res.json().catch(() => null)) as { error?: string } | null
    onEvent({ type: "error", message: message?.error ?? res.statusText } as unknown as TEvent)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    let separatorIndex: number
    while ((separatorIndex = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, separatorIndex)
      buffer = buffer.slice(separatorIndex + 2)

      const line = chunk.split("\n").find((l) => l.startsWith("data: "))
      if (line) {
        onEvent(JSON.parse(line.slice(6)) as TEvent)
      }
    }
  }
}

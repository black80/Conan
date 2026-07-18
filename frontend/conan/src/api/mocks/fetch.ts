import * as store from "./store"

const LABEL_RE = /^\/api\/cases\/([^/]+)\/label$/

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (typeof init?.body !== "string") return {}
  try {
    return JSON.parse(init.body) as Record<string, unknown>
  } catch {
    return {}
  }
}

/**
 * Mock twin of apiFetch: routes the same paths server.py exposes onto the in-memory
 * store. Small artificial latency so loading states render the way they do live.
 */
export async function mockFetch<T>(path: string, init?: RequestInit): Promise<T> {
  await wait(150 + Math.random() * 200)
  const method = (init?.method ?? "GET").toUpperCase()
  const body = parseBody(init)
  return route(path, method, body) as T
}

function route(path: string, method: string, body: Record<string, unknown>): unknown {
  if (path === "/api/alerts") return store.getQueue()

  const labelMatch = path.match(LABEL_RE)
  if (labelMatch && method === "POST") {
    return store.addLabel(
      labelMatch[1],
      body.label as Parameters<typeof store.addLabel>[1],
      typeof body.note === "string" ? body.note : ""
    )
  }

  if (path === "/api/tuning/failures") return store.getFailures()

  if (path === "/api/tuning/approve" && method === "POST") {
    const cards = Array.isArray(body.cards) ? body.cards : []
    if (cards.length === 0) throw new Error("no cards to install")
    return store.approveReview(cards.length)
  }

  if (path === "/api/missed") return store.getMissed()

  if (path === "/api/reports") {
    if (method === "POST") {
      return store.addReport(body as Parameters<typeof store.addReport>[0])
    }
    return store.getReports()
  }

  if (path === "/api/rule_lab/backtest" && method === "POST") return store.getBacktest()

  if (path === "/api/rules") {
    if (method === "POST") {
      return store.addRule(
        body.rule as Parameters<typeof store.addRule>[0],
        body.backtest as Parameters<typeof store.addRule>[1],
        typeof body.report_id === "string" ? body.report_id : undefined
      )
    }
    return store.getRules()
  }

  throw new Error(`no mock for ${method} ${path}`)
}

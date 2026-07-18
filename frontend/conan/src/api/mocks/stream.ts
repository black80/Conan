import type { Case, EvidenceSupport } from "@/api/types"

import * as store from "./store"
import type { ToolStep } from "./store"

type AnyEvent = { type: string }
/** Loosely-typed emission target: the literals here mirror the real backend's SSE
 * payloads, and each transport's generic TEvent narrows them at the call site. */
type MockEvent = { type: string; [key: string]: unknown }
type Emit = (event: MockEvent) => void

const ASK_RE = /^\/api\/cases\/([^/]+)\/ask$/
const RERUN_RE = /^\/api\/tuning\/rerun\/([^/]+)$/

type Timeline = Array<{ delay: number; run: () => void }>

/**
 * Plays a timeline of scheduled emissions with real setTimeout gaps so the UI's
 * streaming states render exactly as they do against the live backend. Cancellable
 * from either transport shape: the returned cancel doubles as the EventSource
 * unsubscribe, and the promise resolves when the script finishes or is aborted.
 */
function play(
  timeline: Timeline,
  signal?: AbortSignal
): { promise: Promise<void>; cancel: () => void } {
  let timer: number | undefined
  let index = 0
  let finish!: () => void
  const promise = new Promise<void>((resolve) => {
    finish = resolve
  })
  const cancel = () => {
    if (timer !== undefined) clearTimeout(timer)
    signal?.removeEventListener("abort", cancel)
    finish()
  }
  const step = () => {
    if (index >= timeline.length) {
      cancel()
      return
    }
    const { delay, run } = timeline[index++]
    timer = window.setTimeout(() => {
      run()
      step()
    }, delay)
  }
  signal?.addEventListener("abort", cancel)
  step()
  return { promise, cancel }
}

/** tool / tool_done pairs for one recorded step, with live-feeling gaps. */
function toolTimeline(steps: ToolStep[], emit: Emit): Timeline {
  return steps.flatMap((step, i) => [
    {
      delay: 350 + (i % 3) * 150,
      run: () => emit({ type: "tool", name: step.name, label: step.label, args: step.args }),
    },
    {
      delay: 300 + ((i + 1) % 2) * 200,
      run: () => emit({ type: "tool_done", name: step.name, is_error: false }),
    },
  ])
}

// ------------------------------------------------------------- GET /api/investigate

/**
 * Mock twin of streamEventSource: replays the alert's recorded investigation — the
 * real tool calls parsed from its reasoning_trace — and terminates in the recorded
 * Case, which is written back to the store so the 3s queue poll keeps it filled.
 */
export function mockEventSource<TEvent extends AnyEvent>(
  path: string,
  params: Record<string, string>,
  onEvent: (event: TEvent) => void
): () => void {
  const emit = onEvent as unknown as Emit
  if (path !== "/api/investigate") {
    emit({ type: "error", message: `no mock stream for ${path}` })
    return () => undefined
  }
  const alertId = params.id ?? ""
  const finalCase = store.getFiledCase(alertId) ?? store.getPendingCase(alertId)
  if (!finalCase) {
    emit({ type: "error", message: "unknown alert" })
    return () => undefined
  }

  const timeline: Timeline = [
    ...toolTimeline(store.getSteps(finalCase.case_id), emit),
    {
      delay: 400,
      run: () => emit({ type: "filing", label: "Weighing the evidence and filing the case" }),
    },
    {
      delay: 1200,
      run: () => {
        store.resolveInvestigation(alertId)
        emit({ type: "done", case: finalCase, truth: { real: store.getTruth(alertId) } })
      },
    },
  ]
  return play(timeline).cancel
}

// ----------------------------------------------------------------- POST SSE routes

/** Mock twin of sseFetch: scripted replays for ask / review / rerun / propose. */
export function mockSseFetch<TEvent extends AnyEvent>(
  path: string,
  body: unknown,
  onEvent: (event: TEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const emit = onEvent as unknown as Emit
  const params = (body ?? {}) as Record<string, unknown>

  const askMatch = path.match(ASK_RE)
  if (askMatch) return playAsk(askMatch[1], emit, signal)
  if (path === "/api/tuning/review") return playReview(emit, signal)
  const rerunMatch = path.match(RERUN_RE)
  if (rerunMatch) return playRerun(rerunMatch[1], emit, signal)
  if (path === "/api/rule_lab/propose") {
    return playPropose(typeof params.report_id === "string" ? params.report_id : "", emit, signal)
  }

  emit({ type: "error", message: `no mock stream for ${path}` })
  return Promise.resolve()
}

// --------------------------------------------------------------------------- ask

function firstSentence(text: string): string {
  const sentence = text.split(". ")[0]
  return sentence.length > 220 ? `${sentence.slice(0, 220)}…` : sentence
}

/** A grounded follow-up answer assembled from the case's own evidence ledger. */
function synthesizeAnswer(filed: Case): string {
  const pick = (supports: EvidenceSupport, n: number) =>
    filed.evidence.filter((e) => e.supports === supports).slice(0, n)
  const bullets = [...pick("fraud", 2), ...pick("legit", 2)].map(
    (e) => `- **${e.label}** — ${firstSentence(e.value)}`
  )
  return (
    `Going back over the file for \`${filed.alert.subject_account}\`:\n\n` +
    `${bullets.join("\n")}\n\n` +
    `On balance, that's why I recommended **${filed.recommendation}** at ` +
    `${Math.round(filed.confidence * 100)}% confidence. Happy to trace any of these further.`
  )
}

function playAsk(caseId: string, emit: Emit, signal?: AbortSignal): Promise<void> {
  const filed = store.getCaseById(caseId)
  if (!filed) {
    emit({ type: "error", message: "unknown case" })
    return Promise.resolve()
  }
  const account = filed.alert.subject_account
  const timeline: Timeline = [
    {
      delay: 500,
      run: () =>
        emit({
          type: "tool",
          name: "get_account_history",
          label: "Reading the last 7 days of transactions",
          args: { account, days: 7 },
        }),
    },
    { delay: 800, run: () => emit({ type: "tool_done", name: "get_account_history", is_error: false }) },
    {
      delay: 350,
      run: () =>
        emit({
          type: "tool",
          name: "get_counterparties",
          label: "Listing counterparties (who this account pays)",
          args: { account, direction: "out" },
        }),
    },
    { delay: 900, run: () => emit({ type: "tool_done", name: "get_counterparties", is_error: false }) },
    { delay: 600, run: () => emit({ type: "done", answer: synthesizeAnswer(filed) }) },
  ]
  return play(timeline, signal).promise
}

// ------------------------------------------------------------------------- review

function chunkText(text: string, size = 45): string[] {
  const chunks: string[] = []
  let current = ""
  for (const word of text.split(" ")) {
    current = current ? `${current} ${word}` : word
    if (current.length >= size) {
      chunks.push(`${current} `)
      current = ""
    }
  }
  if (current) chunks.push(current)
  return chunks
}

function playReview(emit: Emit, signal?: AbortSignal): Promise<void> {
  const review = store.getReview()
  const timeline: Timeline = [
    { delay: 500, run: () => emit({ type: "start", n: review.reflections.length }) },
  ]
  for (const reflection of review.reflections) {
    const label = store.getLatestLabel(reflection.case_id)
    const filed = store.getCaseById(reflection.case_id)
    timeline.push({
      delay: 700,
      run: () =>
        emit({
          type: "reflect_start",
          case_id: reflection.case_id,
          old: {
            recommendation: label?.agent_recommendation ?? filed?.recommendation ?? "approve",
            confidence: label?.agent_confidence ?? filed?.confidence ?? 0.5,
          },
          label: reflection.label,
        }),
    })
    for (const chunk of chunkText(reflection.post_mortem)) {
      timeline.push({
        delay: 90,
        run: () => emit({ type: "reflect_delta", case_id: reflection.case_id, text: chunk }),
      })
    }
    timeline.push({
      delay: 250,
      run: () => emit({ type: "reflect_done", case_id: reflection.case_id }),
    })
  }
  timeline.push({
    delay: 500,
    run: () =>
      emit({ type: "synthesizing", label: "Distilling error patterns into correction cards" }),
  })
  timeline.push({ delay: 1600, run: () => emit({ type: "done", review }) })
  return play(timeline, signal).promise
}

// -------------------------------------------------------------------------- rerun

function playRerun(caseId: string, emit: Emit, signal?: AbortSignal): Promise<void> {
  const filed = store.getCaseById(caseId)
  if (!filed || !store.getLatestLabel(caseId)) {
    emit({ type: "error", message: "unknown case" })
    return Promise.resolve()
  }
  const steps = store.getSteps(caseId).slice(0, 4)
  const timeline: Timeline = [
    ...toolTimeline(steps, emit),
    { delay: 400, run: () => emit({ type: "filing", label: "Weighing the evidence and filing the case" }) },
    // The rerun record is computed at completion time so attempt numbering reflects
    // any reruns that happened earlier in the session.
    { delay: 1100, run: () => emit({ type: "done", ...store.performRerun(caseId) }) },
  ]
  return play(timeline, signal).promise
}

// ------------------------------------------------------------------------ propose

function playPropose(reportId: string, emit: Emit, signal?: AbortSignal): Promise<void> {
  const report = store.getReport(reportId)
  if (!report) {
    emit({ type: "error", message: "unknown report_id" })
    return Promise.resolve()
  }
  const { rule, backtest } = store.getProposal(reportId)
  const timeline: Timeline = [
    {
      delay: 400,
      run: () =>
        emit({
          type: "resolving",
          label: `Resolving ${report.subject_account}'s transactions and feature profile for the reported window`,
        }),
    },
    { delay: 900, run: () => emit({ type: "resolved", n_txns: report.n_txns }) },
    {
      delay: 500,
      run: () =>
        emit({
          type: "proposing",
          label: "Agent is contrasting the case against population quantiles and drafting a rule",
        }),
    },
    { delay: 1700, run: () => emit({ type: "rule_draft", rule }) },
    {
      delay: 700,
      run: () =>
        emit({ type: "backtesting", label: "Simulating the rule over all 5M transactions (17 days)" }),
    },
    { delay: 1700, run: () => emit({ type: "done", rule, backtest, report_id: reportId }) },
  ]
  return play(timeline, signal).promise
}

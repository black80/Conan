import * as React from "react"
import { CheckIcon, ChevronRightIcon, Loader2Icon, TriangleAlertIcon } from "lucide-react"

import { cn, titleCase } from "@/lib/utils"
import type { FilingEvent, ToolDoneEvent, ToolEvent } from "@/api/types"

type ActivityEvent = ToolEvent | ToolDoneEvent | FilingEvent

function isActivityEvent(event: { type: string }): event is ActivityEvent {
  return event.type === "tool" || event.type === "tool_done" || event.type === "filing"
}

type Step = {
  key: string
  label: string
  state: "active" | "done" | "error"
  detail?: string
}

/** Compact "account: 810BB59D0 · days: 7" rendering of a tool call's arguments. */
function formatArgs(args: unknown): string | undefined {
  if (!args || typeof args !== "object") return undefined
  const entries = Object.entries(args as Record<string, unknown>)
  if (entries.length === 0) return undefined
  return entries.map(([key, value]) => `${key}: ${JSON.stringify(value)}`).join(" · ")
}

/** Folds the shared tool/tool_done/filing event vocabulary (BACKEND.md §2) into a step list. */
function buildSteps(events: ActivityEvent[]): Step[] {
  const steps: Step[] = []
  const openByName = new Map<string, number>()

  events.forEach((event, index) => {
    if (event.type === "tool") {
      steps.push({
        key: `${event.name}-${index}`,
        label: event.label,
        state: "active",
        detail: formatArgs(event.args),
      })
      openByName.set(event.name, steps.length - 1)
    } else if (event.type === "tool_done") {
      const stepIndex = openByName.get(event.name)
      if (stepIndex !== undefined && steps[stepIndex]) {
        steps[stepIndex] = {
          ...steps[stepIndex],
          state: event.is_error ? "error" : "done",
        }
        openByName.delete(event.name)
      }
    } else if (event.type === "filing") {
      steps.push({ key: `filing-${index}`, label: event.label, state: "active" })
    }
  })

  return steps
}

/** The stream's own terminal event, if it has finished -- distinct from any single
 * tool call's is_error, since the agent can recover from a failed tool and still
 * finish normally. Drives both "spinner never stops" and "Completed vs Stopped". */
function streamOutcome(events: { type: string }[]): "done" | "error" | null {
  const lastEvent = events[events.length - 1]
  if (lastEvent?.type === "done") return "done"
  if (lastEvent?.type === "error") return "error"
  return null
}

/** The "filing" step (and any tool left dangling by a dropped connection) has no
 * closing event of its own -- resolve it once the stream itself reports done/error,
 * otherwise its spinner would spin forever after the stream has already finished. */
function finalizeTrailingSteps(steps: Step[], outcome: "done" | "error" | null): Step[] {
  if (!outcome) return steps
  return steps.map((step) => (step.state === "active" ? { ...step, state: outcome } : step))
}

/** Shared visual: a connected vertical timeline, steps expandable when they carry detail.
 * Used for both the live SSE view and the historical (reasoning_trace) view so a completed
 * case reads the same way a running one does. */
function TimelineList({ steps, headerText }: { steps: Step[]; headerText: string }) {
  const [expandedKey, setExpandedKey] = React.useState<string | null>(null)

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium text-muted-foreground">{headerText}</p>
      <ul>
        {steps.map((step, i) => {
          const isLast = i === steps.length - 1
          const isExpanded = expandedKey === step.key

          return (
            <li key={step.key} className="flex animate-in fade-in slide-in-from-top-0.5 gap-3">
              <div className="flex flex-col items-center">
                <span className="z-10 flex size-[18px] shrink-0 items-center justify-center rounded-full bg-background">
                  {step.state === "active" && (
                    <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
                  )}
                  {step.state === "done" && (
                    <CheckIcon className="size-4" style={{ color: "var(--chart-2)" }} />
                  )}
                  {step.state === "error" && (
                    <TriangleAlertIcon className="size-4 text-destructive" />
                  )}
                </span>
                {!isLast && <span className="w-[1.5px] flex-1 bg-muted-foreground/30" />}
              </div>
              <div className={cn("min-w-0 flex-1", !isLast && "pb-3")}>
                <button
                  type="button"
                  disabled={!step.detail}
                  onClick={() => setExpandedKey(isExpanded ? null : step.key)}
                  className={cn(
                    "-ml-1.5 flex w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left text-sm",
                    step.detail && "cursor-pointer hover:bg-muted/60",
                    step.state === "done" && "text-muted-foreground"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{step.label}</span>
                  {step.detail && (
                    <ChevronRightIcon
                      className={cn(
                        "size-3.5 shrink-0 text-muted-foreground transition-transform",
                        isExpanded && "rotate-90"
                      )}
                    />
                  )}
                </button>
                {isExpanded && step.detail && (
                  <div className="mt-1 ml-1.5 overflow-x-auto rounded-md border bg-muted px-2 py-1 font-mono text-xs whitespace-pre text-muted-foreground">
                    {step.detail}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** Live view: renders as the SSE stream (investigate / re-investigate / ask) arrives. */
export function AgentActivityTimeline({ events }: { events: { type: string }[] }) {
  const outcome = streamOutcome(events)
  const steps = finalizeTrailingSteps(buildSteps(events.filter(isActivityEvent)), outcome)

  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No activity yet.</p>
  }

  const doneCount = steps.filter((step) => step.state !== "active").length
  const isRunning = outcome === null
  const headerText = isRunning
    ? `Investigating · step ${Math.min(doneCount + 1, steps.length)} of ${steps.length}`
    : outcome === "error"
      ? `Stopped · ${steps.length} step${steps.length === 1 ? "" : "s"}`
      : `Completed · ${steps.length} step${steps.length === 1 ? "" : "s"}`

  return <TimelineList steps={steps} headerText={headerText} />
}

const TRACE_CALL_RE = /^(\w+)\(/

/** reasoning_trace lines look like "tool_name(args) -> result" (agent.py's trace.append),
 * or "tool_name(args) -> ERROR: ..." for a failed call, or a bare "nudge: ..." recovery note. */
function parseTraceLine(line: string, index: number): Step {
  const arrowIndex = line.indexOf(" -> ")
  const call = arrowIndex === -1 ? line : line.slice(0, arrowIndex)
  const result = arrowIndex === -1 ? "" : line.slice(arrowIndex + 4)
  const match = call.match(TRACE_CALL_RE)

  return {
    key: `trace-${index}`,
    label: match ? titleCase(match[1]) : line,
    state: result.startsWith("ERROR:") ? "error" : "done",
    detail: line,
  }
}

/** Historical view: a finished case has no SSE events, only Case.reasoning_trace -- parse
 * it into the same step shape so a completed case's activity reads like a running one. */
export function ReasoningTraceTimeline({ trace }: { trace: string[] }) {
  if (trace.length === 0) {
    return <p className="text-sm text-muted-foreground">No activity recorded.</p>
  }

  const steps = trace.map(parseTraceLine)
  return (
    <TimelineList
      steps={steps}
      headerText={`Completed · ${steps.length} step${steps.length === 1 ? "" : "s"}`}
    />
  )
}

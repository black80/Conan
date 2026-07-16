import { CheckIcon, Loader2Icon, TriangleAlertIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import type { FilingEvent, ToolDoneEvent, ToolEvent } from "@/api/types"

type ActivityEvent = ToolEvent | ToolDoneEvent | FilingEvent

type Step = {
  key: string
  label: string
  state: "active" | "done" | "error"
}

/** Folds the shared tool/tool_done/filing event vocabulary (BACKEND.md §2) into a step list. */
function buildSteps(events: ActivityEvent[]): Step[] {
  const steps: Step[] = []
  const openByName = new Map<string, number>()

  events.forEach((event, index) => {
    if (event.type === "tool") {
      steps.push({ key: `${event.name}-${index}`, label: event.label, state: "active" })
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

export function AgentActivityTimeline({ events }: { events: ActivityEvent[] }) {
  const steps = buildSteps(events)

  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No activity yet.</p>
  }

  return (
    <ul className="space-y-2">
      {steps.map((step) => (
        <li key={step.key} className="flex items-center gap-2 text-sm">
          {step.state === "active" && (
            <Loader2Icon className="size-4 shrink-0 animate-spin text-muted-foreground" />
          )}
          {step.state === "done" && (
            <CheckIcon className="size-4 shrink-0" style={{ color: "var(--chart-2)" }} />
          )}
          {step.state === "error" && (
            <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
          )}
          <span className={cn(step.state === "active" && "text-muted-foreground")}>
            {step.label}
          </span>
        </li>
      ))}
    </ul>
  )
}

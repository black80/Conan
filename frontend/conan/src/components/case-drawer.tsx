import * as React from "react"
import { Loader2Icon, SendIcon } from "lucide-react"
import { toast } from "sonner"

import { investigate } from "@/api/alerts"
import { askCase, labelCase } from "@/api/cases"
import type {
  AskEvent,
  Case,
  ChatTurn,
  HumanLabel,
  InvestigateEvent,
  QueueEntry,
  ToolDoneEvent,
  ToolEvent,
} from "@/api/types"
import { AgentActivityTimeline, ReasoningTraceTimeline } from "@/components/agent-activity-timeline"
import { DisagreementBadge } from "@/components/disagreement-badge"
import { EvidenceLedger } from "@/components/evidence-ledger"
import { MarkdownText } from "@/components/markdown-text"
import { RecommendationBadge } from "@/components/recommendation-badge"
import { TypologyBadge } from "@/components/typology-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useAgentStream } from "@/hooks/use-agent-stream"
import { cn } from "@/lib/utils"
import { useAlerts } from "@/state/alerts-context"

export function CaseDrawer({
  alertId,
  onOpenChange,
}: {
  alertId: string | null
  onOpenChange: (open: boolean) => void
}) {
  const { alerts } = useAlerts()
  const entry = alerts.find((a) => a.alert_id === alertId) ?? null

  return (
    <Sheet open={!!alertId} onOpenChange={(open) => !open && onOpenChange(false)}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-[50vw]">
        {entry && <CaseDrawerBody key={entry.alert_id} entry={entry} />}
      </SheetContent>
    </Sheet>
  )
}

function CaseDrawerBody({ entry }: { entry: QueueEntry }) {
  const { alerts, truthByAlertId, investigations, ensureInvestigating, applyCase, applyTruth } =
    useAlerts()
  const live = alerts.find((a) => a.alert_id === entry.alert_id) ?? entry
  const truth = truthByAlertId[live.alert_id]
  const investigation = investigations[live.alert_id]

  // Idempotent: if the queue row already started this alert's investigation, this is a
  // no-op — it only actually starts one if the drawer somehow opened before the row did.
  React.useEffect(() => {
    if (!live.case) ensureInvestigating(live.alert_id)
  }, [live.case, live.alert_id, ensureInvestigating])

  // Re-investigate (on an already-filed case) is a distinct, explicit repeat action, so it
  // gets its own one-off stream rather than going through the shared investigations map.
  const reinvestigateStream = useAgentStream<InvestigateEvent>()
  const lastReinvestigateEvent =
    reinvestigateStream.events[reinvestigateStream.events.length - 1]
  React.useEffect(() => {
    if (lastReinvestigateEvent?.type === "done") {
      applyCase(live.alert_id, lastReinvestigateEvent.case)
      applyTruth(live.alert_id, lastReinvestigateEvent.truth.real)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastReinvestigateEvent])

  const runReinvestigate = () =>
    reinvestigateStream.start((onEvent) => investigate(live.alert_id, onEvent))

  if (!live.case) {
    return (
      <div className="flex h-full flex-col">
        <SheetHeader className="border-b">
          <SheetTitle>{live.alert_id}</SheetTitle>
          <SheetDescription className="font-mono">{live.subject_account}</SheetDescription>
        </SheetHeader>
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6">
          <div className="w-full max-w-sm space-y-3">
            <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              Investigating…
            </div>
            {investigation && <AgentActivityTimeline events={investigation.events} />}
          </div>
        </div>
      </div>
    )
  }

  const currentCase = live.case

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <SheetHeader className="border-b">
        <div className="flex items-center justify-between gap-2">
          <SheetTitle>{currentCase.case_id}</SheetTitle>
          {typeof truth === "boolean" && (
            <Badge variant={truth ? "destructive" : "secondary"}>
              {truth ? "Ground truth: fraud" : "Ground truth: legitimate"}
            </Badge>
          )}
        </div>
        <SheetDescription className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{live.subject_account}</span>
          <RecommendationBadge recommendation={currentCase.recommendation} />
          <span>{Math.round(currentCase.confidence * 100)}% confidence</span>
        </SheetDescription>
      </SheetHeader>

      <LabelBar
        entry={live}
        onReinvestigate={runReinvestigate}
        reinvestigating={reinvestigateStream.status === "streaming"}
      />

      <Tabs defaultValue="summary" className="flex flex-1 flex-col overflow-hidden px-4 pb-4">
        <TabsList className="mt-3">
          <TabsTrigger value="summary">Case summary</TabsTrigger>
          <TabsTrigger value="activity">Agent activity</TabsTrigger>
          <TabsTrigger value="ask">Ask the agent</TabsTrigger>
        </TabsList>
        <TabsContent value="summary" className="overflow-y-auto pt-4">
          <SummaryTab case={currentCase} />
        </TabsContent>
        <TabsContent value="activity" className="overflow-y-auto pt-4">
          {reinvestigateStream.events.length > 0 ? (
            <AgentActivityTimeline events={reinvestigateStream.events} />
          ) : (
            <ReasoningTraceTimeline trace={currentCase.reasoning_trace} />
          )}
        </TabsContent>
        <TabsContent value="ask" className="flex flex-1 flex-col overflow-hidden pt-4">
          <AskTab caseId={currentCase.case_id} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function LabelBar({
  entry,
  onReinvestigate,
  reinvestigating,
}: {
  entry: QueueEntry
  onReinvestigate: () => void
  reinvestigating: boolean
}) {
  const { applyLabel } = useAlerts()
  const [note, setNote] = React.useState("")
  const [submitting, setSubmitting] = React.useState<HumanLabel | null>(null)

  if (entry.label) {
    return (
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm">
          <Badge variant="outline">{entry.label.label}</Badge>
          <span className="text-muted-foreground">by {entry.label.labeled_by}</span>
          {entry.label.disagreement && <DisagreementBadge severe={entry.label.severe} />}
        </div>
        <Button size="sm" variant="outline" onClick={onReinvestigate} disabled={reinvestigating}>
          {reinvestigating ? "Investigating…" : "Re-investigate"}
        </Button>
      </div>
    )
  }

  const submit = async (label: HumanLabel) => {
    if (!entry.case) return
    setSubmitting(label)
    try {
      const record = await labelCase(entry.case.case_id, label, note.trim() || undefined)
      applyLabel(entry.alert_id, {
        label: record.label,
        final_decision: record.final_decision,
        disagreement: record.disagreement,
        severe: record.severe,
        labeled_by: record.labeled_by,
        ts: record.ts,
      })
      toast.success(`Case labeled ${record.label}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to label case")
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <div className="space-y-2 border-b px-4 py-3">
      <Textarea
        placeholder="Add a note (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className="min-h-16"
      />
      <div className="flex gap-2">
        <Button size="sm" disabled={submitting !== null} onClick={() => submit("mule")}>
          {submitting === "mule" ? "Filing…" : "Mule"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={submitting !== null}
          onClick={() => submit("normal")}
        >
          {submitting === "normal" ? "Filing…" : "Normal"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={submitting !== null}
          onClick={() => submit("suspicious")}
        >
          {submitting === "suspicious" ? "Filing…" : "Suspicious"}
        </Button>
      </div>
    </div>
  )
}

function SummaryTab({ case: currentCase }: { case: Case }) {
  return (
    <div className="space-y-4">
      <MarkdownText text={currentCase.summary} />
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Typology:</span>
        <TypologyBadge typology={currentCase.typology} />
      </div>
      <EvidenceLedger evidence={currentCase.evidence} />
      <p className="text-xs text-muted-foreground">
        {currentCase._meta.model} · prompt {currentCase._meta.prompt_version} ·{" "}
        {currentCase._meta.tool_calls} tool calls
      </p>
    </div>
  )
}

function ChatLoadingIndicator() {
  return (
    <div className="flex items-center gap-1 py-0.5" aria-label="Waiting for response">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  )
}

function AskTab({ caseId }: { caseId: string }) {
  const [history, setHistory] = React.useState<ChatTurn[]>([])
  const [question, setQuestion] = React.useState("")
  const { events, status, start, reset } = useAgentStream<AskEvent>()

  const lastEvent = events[events.length - 1]
  React.useEffect(() => {
    if (lastEvent?.type === "done") {
      setHistory((prev) => [...prev, { role: "assistant", content: lastEvent.answer }])
      reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent])

  const send = () => {
    const q = question.trim()
    if (!q || status === "streaming") return
    const priorHistory = history
    setHistory((prev) => [...prev, { role: "user", content: q }])
    setQuestion("")
    start((onEvent, signal) => askCase(caseId, q, priorHistory, onEvent, signal))
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex-1 space-y-3 overflow-y-auto">
        {history.length === 0 && status !== "streaming" && (
          <p className="text-sm text-muted-foreground">Ask a follow-up about this case.</p>
        )}
        {history.map((turn, i) => (
          <div
            key={i}
            className={cn(
              "max-w-[85%] rounded-lg px-3 py-2 text-sm",
              turn.role === "user" ? "ml-auto bg-primary text-primary-foreground" : "bg-muted"
            )}
          >
            <MarkdownText text={turn.content} />
          </div>
        ))}
        {status === "streaming" && (
          <div className="max-w-[85%] space-y-2 rounded-lg bg-muted px-3 py-2 text-sm">
            {events.some((e) => e.type === "tool" || e.type === "tool_done") ? (
              <AgentActivityTimeline
                events={events.filter(
                  (e): e is ToolEvent | ToolDoneEvent =>
                    e.type === "tool" || e.type === "tool_done"
                )}
              />
            ) : (
              <ChatLoadingIndicator />
            )}
          </div>
        )}
        {status === "error" && (
          <p className="text-sm text-destructive">Something went wrong answering that.</p>
        )}
      </div>
      <div className="flex gap-2">
        <Textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Did the money go to new counterparties?"
          className="min-h-10 flex-1"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <Button size="icon" onClick={send} disabled={status === "streaming" || !question.trim()}>
          <SendIcon />
        </Button>
      </div>
    </div>
  )
}

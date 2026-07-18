import * as React from "react"
import { Loader2Icon } from "lucide-react"
import { toast } from "sonner"

import { approveReview, getFailures, rerunCase, reviewFailures } from "@/api/tuning"
import type {
  FailureRow,
  HumanLabel,
  RerunDiff,
  RerunOutcome,
  ReviewEvent,
  ReviewResult,
} from "@/api/types"
import { DataCeilingBadge, DisagreementBadge } from "@/components/disagreement-badge"
import { MarkdownText } from "@/components/markdown-text"
import { RecommendationBadge } from "@/components/recommendation-badge"
import { RerunOutcomeBadge } from "@/components/rerun-outcome-badge"
import { TablePagination } from "@/components/table-pagination"
import { TypologyBadge } from "@/components/typology-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useAgentStream } from "@/hooks/use-agent-stream"
import { usePagination } from "@/hooks/use-pagination"
import { titleCase } from "@/lib/utils"

type ReflectCard = {
  case_id: string
  label: HumanLabel
  old: { recommendation: string; confidence: number }
  text: string
  done: boolean
}

type ReviewDerived = {
  total: number | null
  cards: ReflectCard[]
  synthesizing: boolean
  result: ReviewResult | null
}

function deriveReview(events: ReviewEvent[]): ReviewDerived {
  const cards: ReflectCard[] = []
  const indexByCase = new Map<string, number>()
  let total: number | null = null
  let synthesizing = false
  let result: ReviewResult | null = null

  for (const event of events) {
    if (event.type === "start") {
      total = event.n
    } else if (event.type === "reflect_start") {
      cards.push({ case_id: event.case_id, label: event.label, old: event.old, text: "", done: false })
      indexByCase.set(event.case_id, cards.length - 1)
    } else if (event.type === "reflect_delta") {
      const idx = indexByCase.get(event.case_id)
      if (idx !== undefined) cards[idx] = { ...cards[idx], text: cards[idx].text + event.text }
    } else if (event.type === "reflect_done") {
      const idx = indexByCase.get(event.case_id)
      if (idx !== undefined) cards[idx] = { ...cards[idx], done: true }
      synthesizing = false
    } else if (event.type === "synthesizing") {
      synthesizing = true
    } else if (event.type === "done") {
      result = event.review
      synthesizing = false
    }
  }

  return { total, cards, synthesizing, result }
}

type RerunState = {
  status: "running" | "done" | "error"
  toolCalls: number
  outcome?: RerunOutcome
  diff?: RerunDiff
}

function DiffLine({ diff }: { diff: RerunDiff }) {
  return (
    <p className="text-xs text-muted-foreground">
      {diff.recommendation[0]} ({Math.round(diff.confidence[0] * 100)}%) →{" "}
      {diff.recommendation[1]} ({Math.round(diff.confidence[1] * 100)}%)
      {diff.typology[0] !== diff.typology[1] && (
        <>
          {" · "}
          {diff.typology[0] ? titleCase(diff.typology[0]) : "—"} →{" "}
          {diff.typology[1] ? titleCase(diff.typology[1]) : "—"}
        </>
      )}
    </p>
  )
}

export function ModelTuningPage() {
  const [failures, setFailures] = React.useState<FailureRow[]>([])
  const [loading, setLoading] = React.useState(true)
  const [installedVersion, setInstalledVersion] = React.useState<number | null>(null)
  const [approving, setApproving] = React.useState(false)
  const [verifying, setVerifying] = React.useState(false)
  const [rerunState, setRerunState] = React.useState<Record<string, RerunState>>({})
  const reviewStream = useAgentStream<ReviewEvent>()
  const failuresPagination = usePagination(failures)

  const loadFailures = React.useCallback(async () => {
    setLoading(true)
    try {
      const data = await getFailures()
      setFailures(data)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load disagreements")
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void loadFailures()
  }, [loadFailures])

  const derived = deriveReview(reviewStream.events)

  const runReview = () => {
    reviewStream.start((onEvent, signal) => reviewFailures(onEvent, signal))
  }

  const approve = async () => {
    if (!derived.result) return
    setApproving(true)
    try {
      const { version, n_cards } = await approveReview({
        cards: derived.result.cards,
        error_patterns: derived.result.error_patterns,
        summary: derived.result.summary,
      })
      setInstalledVersion(version)
      toast.success(`Installed v3+ex${version - 1} with ${n_cards} correction card(s)`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to install the adaptation")
    } finally {
      setApproving(false)
    }
  }

  const runVerification = async () => {
    setVerifying(true)
    const candidates = failures.filter((row) => !row.data_ceiling)

    for (const row of candidates) {
      setRerunState((prev) => ({ ...prev, [row.case_id]: { status: "running", toolCalls: 0 } }))
      try {
        await rerunCase(row.case_id, (event) => {
          if (event.type === "tool_done") {
            setRerunState((prev) => ({
              ...prev,
              [row.case_id]: {
                ...prev[row.case_id],
                status: "running",
                toolCalls: (prev[row.case_id]?.toolCalls ?? 0) + 1,
              },
            }))
          } else if (event.type === "done") {
            setRerunState((prev) => ({
              ...prev,
              [row.case_id]: {
                status: "done",
                toolCalls: prev[row.case_id]?.toolCalls ?? 0,
                outcome: event.outcome,
                diff: event.diff,
              },
            }))
          } else if (event.type === "error") {
            setRerunState((prev) => ({
              ...prev,
              [row.case_id]: { ...prev[row.case_id], status: "error" },
            }))
          }
        })
      } catch {
        setRerunState((prev) => ({ ...prev, [row.case_id]: { ...prev[row.case_id], status: "error" } }))
      }
    }

    setVerifying(false)
    void loadFailures()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Disagreements</CardTitle>
          <CardDescription>
            Cases where the agent's recommendation didn't match the analyst's label.
          </CardDescription>
          <CardAction>
            <Button
              onClick={runReview}
              disabled={reviewStream.status === "streaming" || loading || failures.length === 0}
            >
              {reviewStream.status === "streaming" ? "Reviewing…" : "Self-review disagreements"}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Analyst label</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Typology</TableHead>
                <TableHead>Verify</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!loading && failures.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                    No disagreements yet.
                  </TableCell>
                </TableRow>
              )}
              {failuresPagination.pageItems.map((row) => {
                const rerun = rerunState[row.case_id]
                return (
                  <TableRow key={row.case_id}>
                    <TableCell className="font-mono">{row.case_id}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <RecommendationBadge recommendation={row.old_recommendation} />
                        <span className="text-xs text-muted-foreground">
                          {Math.round(row.old_confidence * 100)}%
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{row.human_label}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <DisagreementBadge severe={row.severe} />
                        {row.data_ceiling && <DataCeilingBadge />}
                      </div>
                    </TableCell>
                    <TableCell>
                      <TypologyBadge typology={row.typology} />
                    </TableCell>
                    <TableCell>
                      {rerun?.status === "running" && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2Icon className="size-3.5 animate-spin" />
                          {rerun.toolCalls} tool call(s)
                        </div>
                      )}
                      {rerun?.status === "done" && rerun.outcome && (
                        <div className="space-y-1">
                          <RerunOutcomeBadge outcome={rerun.outcome} />
                          {rerun.diff && <DiffLine diff={rerun.diff} />}
                        </div>
                      )}
                      {rerun?.status === "error" && <Badge variant="destructive">Failed</Badge>}
                      {!rerun && row.rerun_status && <RerunOutcomeBadge outcome={row.rerun_status} />}
                      {!rerun && !row.rerun_status && (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          <TablePagination
            page={failuresPagination.page}
            pageCount={failuresPagination.pageCount}
            onPageChange={failuresPagination.setPage}
          />
        </CardContent>
      </Card>

      {reviewStream.status !== "idle" && (
        <Card>
          <CardHeader>
            <CardTitle>Agent self-review</CardTitle>
            <CardDescription>
              The agent explaining each disagreement and proposing a correction.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {derived.total !== null && !derived.result && (
              <p className="text-sm text-muted-foreground">
                Reviewing {derived.cards.length} of {derived.total} disagreed case(s)…
              </p>
            )}
            <div className="space-y-3">
              {derived.cards.map((card) => (
                <div key={card.case_id} className="rounded-lg border p-3">
                  <div className="mb-2 flex items-center gap-2 text-sm">
                    {!card.done && <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />}
                    <span className="font-mono">{card.case_id}</span>
                    <span className="text-muted-foreground">
                      {card.old.recommendation} → {card.label}
                    </span>
                  </div>
                  <MarkdownText
                    text={card.text}
                    className="whitespace-pre-wrap text-muted-foreground"
                  />
                </div>
              ))}
            </div>

            {derived.synthesizing && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-3.5 animate-spin" />
                Synthesizing an adaptation…
              </p>
            )}

            {reviewStream.status === "error" && (
              <p className="text-sm text-destructive">Self-review failed.</p>
            )}

            {derived.result && (
              <div className="space-y-3 rounded-lg border p-4">
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground uppercase">
                    Error patterns
                  </p>
                  <ul className="list-inside list-disc text-sm">
                    {derived.result.error_patterns.map((pattern, i) => (
                      <li key={i}>
                        <MarkdownText text={pattern} className="inline" />
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground uppercase">
                    Correction cards ({derived.result.cards.length})
                  </p>
                  <ul className="space-y-2">
                    {derived.result.cards.map((card) => (
                      <li key={card.case_id}>
                        <span className="font-mono text-xs text-muted-foreground">
                          {card.case_id}
                        </span>
                        <MarkdownText text={card.text} />
                      </li>
                    ))}
                  </ul>
                </div>
                <MarkdownText text={derived.result.summary} />
                <div className="flex gap-2">
                  <Button onClick={approve} disabled={approving || installedVersion !== null}>
                    {installedVersion !== null
                      ? "Installed"
                      : approving
                        ? "Installing…"
                        : "Approve & install"}
                  </Button>
                  <Button variant="outline" onClick={reviewStream.reset} disabled={approving}>
                    Discard
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {installedVersion !== null && (
        <Card>
          <CardHeader>
            <CardTitle>Verify the change</CardTitle>
            <CardDescription>
              Re-investigates every disagreement with the newly installed correction cards and
              diffs the old verdict against the new one — regressions are shown, never hidden.
            </CardDescription>
            <CardAction>
              <Button onClick={runVerification} disabled={verifying}>
                {verifying ? "Re-reviewing…" : "Re-review all cases"}
              </Button>
            </CardAction>
          </CardHeader>
        </Card>
      )}
    </div>
  )
}

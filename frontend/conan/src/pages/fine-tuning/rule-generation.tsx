import * as React from "react"
import { Loader2Icon } from "lucide-react"
import { toast } from "sonner"

import {
  backtestRule,
  getMissed,
  getReports,
  getRules,
  postReport,
  proposeRule,
  saveRule,
} from "@/api/ruleLab"
import type { Backtest, MissedCase, ProposeEvent, Report, RuleDsl, RuleEntry } from "@/api/types"
import { BacktestMetrics } from "@/components/backtest-metrics"
import { RuleDslCard } from "@/components/rule-dsl-card"
import { TablePagination } from "@/components/table-pagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
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
import { cn } from "@/lib/utils"

type ProposeStage = "resolving" | "resolved" | "proposing" | "revising" | "backtesting" | null

type ProposeDerived = {
  stage: ProposeStage
  resolvedTxns: number | null
  drafts: RuleDsl[]
  problems: string[] | null
  result: { rule: RuleDsl; backtest: Backtest } | null
  errorMessage: string | null
}

const STAGE_LABEL: Record<Exclude<ProposeStage, null>, string> = {
  resolving: "Resolving victim report to real transactions…",
  resolved: "Transactions resolved.",
  proposing: "Drafting a candidate rule…",
  revising: "Validator rejected the draft — revising…",
  backtesting: "Backtesting over 5M transactions…",
}

function deriveProposal(events: ProposeEvent[]): ProposeDerived {
  let stage: ProposeStage = null
  let resolvedTxns: number | null = null
  const drafts: RuleDsl[] = []
  let problems: string[] | null = null
  let result: { rule: RuleDsl; backtest: Backtest } | null = null
  let errorMessage: string | null = null

  for (const event of events) {
    if (event.type === "resolving") stage = "resolving"
    else if (event.type === "resolved") {
      stage = "resolved"
      resolvedTxns = event.n_txns
    } else if (event.type === "proposing") stage = "proposing"
    else if (event.type === "rule_draft") drafts.push(event.rule)
    else if (event.type === "revising") {
      stage = "revising"
      problems = event.problems
    } else if (event.type === "backtesting") stage = "backtesting"
    else if (event.type === "done") {
      // A later successful draft supersedes an earlier validator rejection — don't keep
      // showing "problems" once the rule that's actually displayed has passed validation.
      result = { rule: event.rule, backtest: event.backtest }
      problems = null
    } else if (event.type === "error") errorMessage = event.message
  }

  return { stage, resolvedTxns, drafts, problems, result, errorMessage }
}

async function ensureReport(missed: MissedCase, reports: Report[]): Promise<Report> {
  const existing = reports.find(
    (r) =>
      r.subject_account === missed.suggested_subject &&
      r.window_start === missed.window_start &&
      r.window_end === missed.window_end
  )
  if (existing) return existing

  return postReport({
    subject_account: missed.suggested_subject,
    window_start: missed.window_start,
    window_end: missed.window_end,
    description: missed.context,
  })
}

function EditableRule({ rule, onChange }: { rule: RuleDsl; onChange: (rule: RuleDsl) => void }) {
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-medium">{rule.name}</span>
        <div className="flex items-center gap-1.5">
          <Badge variant="secondary">{rule.subject_side}</Badge>
          <Badge variant="outline">weight {rule.weight}</Badge>
        </div>
      </div>
      <div className="space-y-2">
        {rule.conjuncts.map((conjunct, i) => (
          <div key={i} className="flex items-center gap-2 text-sm">
            <span className="font-mono text-muted-foreground">
              {conjunct.feature} {conjunct.op}
            </span>
            <Input
              type="number"
              value={conjunct.threshold}
              onChange={(e) => {
                const nextConjuncts = [...rule.conjuncts]
                nextConjuncts[i] = { ...conjunct, threshold: Number(e.target.value) }
                onChange({ ...rule, conjuncts: nextConjuncts })
              }}
              className="h-8 w-32"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

export function RuleGenerationPage() {
  const [missed, setMissed] = React.useState<MissedCase[]>([])
  const [reports, setReports] = React.useState<Report[]>([])
  const [rules, setRules] = React.useState<RuleEntry[]>([])
  const [loading, setLoading] = React.useState(true)
  const [selected, setSelected] = React.useState<MissedCase | null>(null)
  const [reportId, setReportId] = React.useState<string | null>(null)
  const [proposing, setProposing] = React.useState(false)
  const [editableRule, setEditableRule] = React.useState<RuleDsl | null>(null)
  const [latestBacktest, setLatestBacktest] = React.useState<Backtest | null>(null)
  const [rebacktesting, setRebacktesting] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const proposeStream = useAgentStream<ProposeEvent>()
  const missedPagination = usePagination(missed)
  const rulesPagination = usePagination(rules)

  const loadAll = React.useCallback(async () => {
    setLoading(true)
    try {
      const [missedData, reportsData, rulesData] = await Promise.all([
        getMissed(),
        getReports(),
        getRules(),
      ])
      setMissed(missedData)
      setReports(reportsData)
      setRules(rulesData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load Rule Lab data")
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void loadAll()
  }, [loadAll])

  const derived = deriveProposal(proposeStream.events)

  React.useEffect(() => {
    if (derived.result) {
      setEditableRule(derived.result.rule)
      setLatestBacktest(derived.result.backtest)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [derived.result])

  const selectMissed = (row: MissedCase) => {
    setSelected(row)
    setReportId(null)
    setEditableRule(null)
    setLatestBacktest(null)
    proposeStream.reset()
  }

  const propose = async () => {
    if (!selected) return
    setProposing(true)
    try {
      const report = await ensureReport(selected, reports)
      setReportId(report.report_id)
      setReports((prev) => (prev.some((r) => r.report_id === report.report_id) ? prev : [...prev, report]))
      setMissed((prev) =>
        prev.map((m) => (m.attempt_id === selected.attempt_id ? { ...m, reported: true } : m))
      )
      proposeStream.start((onEvent, signal) => proposeRule(report.report_id, onEvent, signal))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to file the victim report")
    } finally {
      setProposing(false)
    }
  }

  const rebacktest = async () => {
    if (!editableRule) return
    setRebacktesting(true)
    try {
      const result = await backtestRule(editableRule, reportId ?? undefined)
      setLatestBacktest(result)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Backtest failed")
    } finally {
      setRebacktesting(false)
    }
  }

  const save = async () => {
    if (!editableRule || !latestBacktest || !reportId) return
    setSaving(true)
    try {
      await saveRule(editableRule, latestBacktest, reportId)
      toast.success(`Saved ${editableRule.name} to the registry`)
      const rulesData = await getRules()
      setRules(rulesData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save the rule")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Missed cases</CardTitle>
          <CardDescription>Fraud reports the current rules failed to flag.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Attempt</TableHead>
                <TableHead>Subject</TableHead>
                <TableHead>Window</TableHead>
                <TableHead>Accounts</TableHead>
                <TableHead>Txns</TableHead>
                <TableHead>Reported</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!loading && missed.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                    No missed cases.
                  </TableCell>
                </TableRow>
              )}
              {missedPagination.pageItems.map((row) => (
                <TableRow
                  key={row.attempt_id}
                  className={cn(
                    "cursor-pointer",
                    selected?.attempt_id === row.attempt_id && "bg-muted/50"
                  )}
                  onClick={() => selectMissed(row)}
                >
                  <TableCell>{row.attempt_id}</TableCell>
                  <TableCell className="font-mono">{row.suggested_subject}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.window_start} → {row.window_end}
                  </TableCell>
                  <TableCell>{row.n_accounts}</TableCell>
                  <TableCell>{row.n_txns}</TableCell>
                  <TableCell>{row.reported && <Badge variant="secondary">✓</Badge>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <TablePagination
            page={missedPagination.page}
            pageCount={missedPagination.pageCount}
            onPageChange={missedPagination.setPage}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Case context</CardTitle>
          <CardDescription>Details for the selected missed case.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!selected && <p className="text-sm text-muted-foreground">Select a missed case.</p>}
          {selected && (
            <>
              <p className="text-sm">{selected.context}</p>
              <Button onClick={propose} disabled={proposing || proposeStream.status === "streaming"}>
                {proposing || proposeStream.status === "streaming" ? "Working…" : "Propose rule"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Lab bench</CardTitle>
          <CardDescription>Draft, edit, and backtest a proposed rule before saving it.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {proposeStream.status === "idle" && !editableRule && (
            <p className="text-sm text-muted-foreground">
              Propose a rule from a selected missed case to see it here.
            </p>
          )}

          {proposeStream.status === "streaming" && derived.stage && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              {STAGE_LABEL[derived.stage]}
              {derived.stage === "resolved" && derived.resolvedTxns !== null && (
                <span>({derived.resolvedTxns} txns)</span>
              )}
            </div>
          )}

          {derived.problems && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <p className="mb-1 font-medium">Validator feedback</p>
              <ul className="list-inside list-disc">
                {derived.problems.map((problem, i) => (
                  <li key={i}>{problem}</li>
                ))}
              </ul>
            </div>
          )}

          {derived.errorMessage && <p className="text-sm text-destructive">{derived.errorMessage}</p>}

          {derived.drafts.length > 0 && !editableRule && (
            <div className="space-y-3">
              {derived.drafts.map((draft, i) => (
                <RuleDslCard key={i} rule={draft} />
              ))}
            </div>
          )}

          {editableRule && (
            <div className="grid gap-4 sm:grid-cols-2">
              <EditableRule rule={editableRule} onChange={setEditableRule} />
              <div className="space-y-3">
                {latestBacktest && <BacktestMetrics backtest={latestBacktest} />}
                <div className="flex gap-2">
                  <Button variant="outline" onClick={rebacktest} disabled={rebacktesting}>
                    {rebacktesting ? "Backtesting…" : "Re-backtest"}
                  </Button>
                  <Button onClick={save} disabled={saving || !latestBacktest || !reportId}>
                    {saving ? "Saving…" : "Save to registry"}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Detection rules</CardTitle>
          <CardDescription>Every rule in the registry, hand-written and agent-proposed.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Weight</TableHead>
                <TableHead>Provenance</TableHead>
                <TableHead>Enabled</TableHead>
                <TableHead>Precision</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rulesPagination.pageItems.map((entry) => (
                <TableRow key={entry.rule.name}>
                  <TableCell className="font-mono">{entry.rule.name}</TableCell>
                  <TableCell>{entry.rule.subject_side}</TableCell>
                  <TableCell>{entry.rule.weight}</TableCell>
                  <TableCell>
                    <Badge variant={entry.provenance === "hand_written" ? "secondary" : "outline"}>
                      {entry.provenance === "hand_written" ? "Hand-written" : "Agent-proposed"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={entry.enabled ? "default" : "secondary"}>
                      {entry.enabled ? "Enabled" : "Disabled"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {entry.backtest ? `${Math.round(entry.backtest.precision * 100)}%` : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <TablePagination
            page={rulesPagination.page}
            pageCount={rulesPagination.pageCount}
            onPageChange={rulesPagination.setPage}
          />
        </CardContent>
      </Card>
    </div>
  )
}

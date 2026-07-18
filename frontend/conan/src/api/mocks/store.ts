import type {
  Backtest,
  Case,
  FailureRow,
  FailureType,
  FinalDecision,
  HumanLabel,
  LabelRecord,
  MissedCase,
  QueueEntry,
  Recommendation,
  Report,
  ReportStatus,
  RerunDiff,
  RerunOutcome,
  ReviewResult,
  RuleDsl,
  RuleEntry,
} from "@/api/types"

import seedJson from "./seed.json"

/** One recorded tool call, parsed from a case's reasoning_trace by gen-mock-seed.mjs. */
export type ToolStep = { name: string; label: string; args: Record<string, unknown> }

type RerunRecord = {
  case_id: string
  attempt: number
  outcome: RerunOutcome
  prompt_version: string
  diff: RerunDiff
  ts: string
}

type Seed = {
  alerts: QueueEntry[]
  /** Recorded Case per pending alert — installed on the queue entry when its scripted investigation finishes. */
  pendingCases: Record<string, Case>
  stepsByCase: Record<string, ToolStep[]>
  truthByAlertId: Record<string, boolean>
  labels: LabelRecord[]
  reruns: RerunRecord[]
  builtinRules: RuleEntry[]
  savedRules: RuleEntry[]
  missed: MissedCase[]
  reports: Report[]
  review: ReviewResult
  proposal: { rule: RuleDsl; backtest: Backtest }
  exemplarVersion: number
}

// The JSON import's inferred type widens unions (e.g. typology) to plain string; the
// data is generated from the recorded backend output, so the cast is the contract.
const seed = seedJson as unknown as Seed

/**
 * Session-lifetime mutable state. GET /api/alerts is polled every 3s and replaces the
 * whole queue, so every mutation (finished investigation, filed label, saved rule…)
 * writes back here — otherwise the next poll would revert it. A page reload restarts
 * the demo from the seed.
 */
const state: Seed = structuredClone(seed)

// ------------------------------------------------------------- loop.py replicas

const AGREES_WITH: Record<HumanLabel, Recommendation> = {
  mule: "block",
  normal: "approve",
  suspicious: "escalate",
}
const FINAL_DECISION: Record<HumanLabel, FinalDecision> = {
  mule: "confirmed_fraud",
  normal: "false_positive",
  suspicious: "escalated",
}

function judge(label: HumanLabel, recommendation: Recommendation) {
  const failureType: FailureType =
    label === "mule" && recommendation === "approve"
      ? "missed_mule"
      : label === "normal" && recommendation === "block"
        ? "false_alarm"
        : null
  return {
    disagreement: AGREES_WITH[label] !== recommendation,
    severe: failureType !== null,
    failure_type: failureType,
    final_decision: FINAL_DECISION[label],
    closes_case: label === "mule" || label === "normal",
  }
}

/** agent/tuning.py outcome(): score a rerun's new recommendation against the label. */
function rerunOutcome(
  oldRec: Recommendation,
  newRec: Recommendation,
  label: HumanLabel
): RerunOutcome {
  const oldRight = AGREES_WITH[label] === oldRec
  const newRight = AGREES_WITH[label] === newRec
  if (newRight) return oldRight ? "still_correct" : "flipped_correct"
  return oldRight ? "regressed" : "still_wrong"
}

const latestBy = <T, K extends keyof T>(rows: T[], key: K): Map<T[K], T> => {
  const map = new Map<T[K], T>()
  for (const row of rows) map.set(row[key], row)
  return map
}

// -------------------------------------------------------------------- accessors

export function getQueue(): QueueEntry[] {
  return structuredClone(state.alerts)
}

function entryByAlertId(alertId: string): QueueEntry | undefined {
  return state.alerts.find((entry) => entry.alert_id === alertId)
}

export function getCaseById(caseId: string): Case | undefined {
  const filed = state.alerts.find((entry) => entry.case?.case_id === caseId)?.case
  if (filed) return filed
  return Object.values(state.pendingCases).find((c) => c.case_id === caseId)
}

export function getPendingCase(alertId: string): Case | undefined {
  return state.pendingCases[alertId]
}

export function getFiledCase(alertId: string): Case | null | undefined {
  return entryByAlertId(alertId)?.case
}

export function getSteps(caseId: string): ToolStep[] {
  return state.stepsByCase[caseId] ?? []
}

export function getTruth(alertId: string): boolean | null {
  return state.truthByAlertId[alertId] ?? null
}

/** A scripted investigation finished: install the case so the 3s poll keeps it. */
export function resolveInvestigation(alertId: string): void {
  const entry = entryByAlertId(alertId)
  const pending = state.pendingCases[alertId]
  if (entry && pending && !entry.case) entry.case = pending
}

export function addLabel(caseId: string, label: HumanLabel, note: string): LabelRecord {
  const filed = getCaseById(caseId)
  if (!filed) throw new Error("unknown case")
  const alertId = filed.alert.alert_id
  const entry = entryByAlertId(alertId)
  const record: LabelRecord = {
    case_id: caseId,
    alert_id: alertId,
    label,
    note,
    agent_recommendation: filed.recommendation,
    agent_confidence: filed.confidence,
    prompt_version: filed._meta.prompt_version,
    labeled_by: entry?.assigned_to ?? "Aisha",
    ts: new Date().toISOString(),
    ...judge(label, filed.recommendation),
  }
  state.labels.push(record)
  if (entry) {
    entry.label = {
      label: record.label,
      final_decision: record.final_decision,
      disagreement: record.disagreement,
      severe: record.severe,
      labeled_by: record.labeled_by,
      ts: record.ts,
    }
  }
  return structuredClone(record)
}

export function getLatestLabel(caseId: string): LabelRecord | undefined {
  return latestBy(state.labels, "case_id").get(caseId)
}

/** agent/tuning.py failures(): every disagreed case, joined with case + latest rerun. */
export function getFailures(): FailureRow[] {
  const rerunByCase = latestBy(state.reruns, "case_id")
  const rows: FailureRow[] = []
  for (const lab of latestBy(state.labels, "case_id").values()) {
    if (!lab.disagreement) continue
    const filed = getCaseById(lab.case_id)
    if (!filed) continue
    const rerun = rerunByCase.get(lab.case_id)
    rows.push({
      case_id: lab.case_id,
      alert_id: lab.alert_id,
      old_recommendation: lab.agent_recommendation,
      old_confidence: lab.agent_confidence,
      human_label: lab.label,
      severe: lab.severe,
      failure_type: lab.failure_type ?? "mild_disagreement",
      typology: filed.typology,
      labeled_by: lab.labeled_by,
      rerun_status: rerun?.outcome ?? null,
      data_ceiling: !!rerun && rerun.outcome === "still_wrong" && rerun.attempt >= 2,
    })
  }
  return structuredClone(rows)
}

export function getReview(): ReviewResult {
  return structuredClone(state.review)
}

export function approveReview(nCards: number): { version: number; n_cards: number } {
  state.exemplarVersion += 1
  return { version: state.exemplarVersion, n_cards: nCards }
}

/**
 * Scripted rerun: with the corrections installed, the agent flips to agree with the
 * analyst (the recorded CS-02475786 history already sits at its data ceiling and is
 * excluded from re-runs by the UI).
 */
export function performRerun(caseId: string): {
  old_case: Case
  new_case: Case
  diff: RerunDiff
  outcome: RerunOutcome
  attempt: number
  data_ceiling: boolean
} {
  const oldCase = getCaseById(caseId)
  const label = getLatestLabel(caseId)
  if (!oldCase || !label) throw new Error("unknown case")
  const newRec = AGREES_WITH[label.label]
  const newConfidence = 0.74
  const newTypology = oldCase.typology ?? "GATHER-SCATTER"
  const outcome = rerunOutcome(oldCase.recommendation, newRec, label.label)
  const attempt = state.reruns.filter((r) => r.case_id === caseId).length + 1
  const diff: RerunDiff = {
    recommendation: [oldCase.recommendation, newRec],
    confidence: [oldCase.confidence, newConfidence],
    typology: [oldCase.typology, newTypology],
    human_label: label.label,
  }
  state.reruns.push({
    case_id: caseId,
    attempt,
    outcome,
    prompt_version: `v3+ex${state.exemplarVersion}`,
    diff,
    ts: new Date().toISOString(),
  })
  const newCase: Case = {
    ...structuredClone(oldCase),
    recommendation: newRec,
    confidence: newConfidence,
    typology: newTypology,
  }
  newCase._meta.prompt_version = `v3+ex${state.exemplarVersion}`
  return {
    old_case: structuredClone(oldCase),
    new_case: newCase,
    diff,
    outcome,
    attempt,
    data_ceiling: outcome === "still_wrong" && attempt >= 2,
  }
}

// --------------------------------------------------------------------- rule lab

export function getMissed(): MissedCase[] {
  const reported = new Set(state.reports.map((r) => r.subject_account))
  return structuredClone(
    state.missed.map((row) => ({ ...row, reported: reported.has(row.suggested_subject) }))
  )
}

function reportStatus(reportId: string): ReportStatus {
  const created = state.savedRules.some((r) => r.source_report_ids?.includes(reportId))
  return created ? "rule_created" : "new"
}

export function getReports(): Report[] {
  return structuredClone(
    state.reports.map((report) => ({ ...report, status: reportStatus(report.report_id) }))
  )
}

export function getReport(reportId: string): Report | undefined {
  return state.reports.find((r) => r.report_id === reportId)
}

export function addReport(body: {
  subject_account: string
  window_start: string
  window_end: string
  description?: string
}): Report {
  const missedRow = state.missed.find(
    (m) =>
      m.suggested_subject === body.subject_account &&
      m.window_start === body.window_start &&
      m.window_end === body.window_end
  )
  const report: Report = {
    report_id: `RPT-${String(state.reports.length + 1).padStart(4, "0")}`,
    subject_account: body.subject_account.trim(),
    window_start: body.window_start,
    window_end: body.window_end,
    description: body.description ?? "",
    n_txns: missedRow?.n_txns ?? 24,
  }
  state.reports.push(report)
  return structuredClone({ ...report, status: "new" as ReportStatus })
}

export function getRules(): RuleEntry[] {
  return structuredClone([...state.builtinRules, ...state.savedRules])
}

export function addRule(rule: RuleDsl, backtest: Backtest, reportId?: string): RuleEntry {
  const entry: RuleEntry = {
    rule,
    backtest,
    provenance: "agent_proposed",
    enabled: false,
    source_report_ids: reportId ? [reportId] : [],
    saved_at: new Date().toISOString(),
  }
  state.savedRules.push(entry)
  return structuredClone(entry)
}

/** The canned proposal, personalized so successive saves don't collide on rule.name. */
export function getProposal(reportId: string): { rule: RuleDsl; backtest: Backtest } {
  const proposal = structuredClone(state.proposal)
  proposal.rule.name = `${proposal.rule.name}_${reportId.replace("RPT-", "R")}`
  return proposal
}

export function getBacktest(): Backtest {
  return structuredClone(state.proposal.backtest)
}

export type SubjectSide = "sender" | "receiver"

export type Txn = {
  txn_id: string
  timestamp: string
  sender_bank: string
  sender_account: string
  receiver_bank: string
  receiver_account: string
  amount_paid: number
  payment_currency: string
  amount_received: number
  receiving_currency: string
  payment_format: string
}

export type Alert = {
  alert_id: string
  txn: Txn
  subject_account: string
  subject_side: SubjectSide
  rules_fired: string[]
  score: number
  features: Record<string, number>
  created_at: string
}

export type Typology =
  | "FAN-IN"
  | "FAN-OUT"
  | "GATHER-SCATTER"
  | "SCATTER-GATHER"
  | "CYCLE"
  | "STACK"
  | "BIPARTITE"
  | "RANDOM"
  | null

export type Recommendation = "approve" | "escalate" | "block"
export type CaseStatus = "auto_closed" | "needs_review"

export type EvidenceSupport = "fraud" | "legit" | "neutral"

export type Evidence = {
  label: string
  value: string
  supports: EvidenceSupport
}

export type CaseMeta = {
  model: string
  prompt_version: string
  tool_calls: number
  usage: Record<string, number>
  created_at: string
}

export type Case = {
  case_id: string
  alert: Alert
  summary: string
  evidence: Evidence[]
  typology: Typology
  recommendation: Recommendation
  confidence: number
  reasoning_trace: string[]
  status: CaseStatus
  _meta: CaseMeta
}

export type HumanLabel = "mule" | "normal" | "suspicious"
export type FinalDecision = "confirmed_fraud" | "false_positive" | "escalated"
export type FailureType = "missed_mule" | "false_alarm" | "mild_disagreement" | null

export type QueueLabel = {
  label: HumanLabel
  final_decision: FinalDecision
  disagreement: boolean
  severe: boolean
  labeled_by: string
  ts: string
}

/** One row of GET /api/alerts — a flattened queue entry, distinct from the full Alert contract nested in Case. */
export type QueueEntry = {
  alert_id: string
  subject_account: string
  timestamp: string
  rules_fired: string[]
  assigned_to: string
  case: Case | null
  label: QueueLabel | null
}

export type LabelRecord = {
  case_id: string
  alert_id: string
  label: HumanLabel
  note: string
  agent_recommendation: Recommendation
  agent_confidence: number
  prompt_version: string
  labeled_by: string
  disagreement: boolean
  severe: boolean
  failure_type: FailureType
  final_decision: FinalDecision
  closes_case: boolean
  ts: string
}

export type ChatTurn = {
  role: "user" | "assistant"
  content: string
}

export type RerunStatus = "flipped_correct" | "still_wrong" | "still_correct" | "regressed" | null

export type FailureRow = {
  case_id: string
  alert_id: string
  old_recommendation: Recommendation
  old_confidence: number
  human_label: HumanLabel
  severe: boolean
  failure_type: FailureType
  typology: Typology
  labeled_by: string
  rerun_status: RerunStatus
  data_ceiling: boolean
}

export type CorrectionCard = {
  case_id: string
  text: string
}

export type Reflection = {
  case_id: string
  label: HumanLabel
  post_mortem: string
}

export type ReviewResult = {
  error_patterns: string[]
  cards: CorrectionCard[]
  summary: string
  reflections: Reflection[]
}

export type RerunOutcome = "flipped_correct" | "still_wrong" | "still_correct" | "regressed"

export type RerunDiff = {
  recommendation: [Recommendation, Recommendation]
  confidence: [number, number]
  typology: [Typology, Typology]
  human_label: HumanLabel
}

export type MissedCase = {
  attempt_id: number
  suggested_subject: string
  window_start: string
  window_end: string
  n_accounts: number
  accounts: string[]
  n_txns: number
  context: string
  reported: boolean
}

export type ReportStatus = "new" | "rule_created"

export type Report = {
  report_id: string
  subject_account: string
  window_start: string
  window_end: string
  description: string
  n_txns: number
  status?: ReportStatus
}

export type RuleOp = ">" | ">=" | "<" | "<="

export type RuleConjunct = {
  feature: string
  op: RuleOp
  threshold: number
}

export type RuleDsl = {
  name: string
  subject_side: SubjectSide
  weight: number
  conjuncts: RuleConjunct[]
  rationale?: string
}

export type Backtest = {
  catches_case: boolean
  new_alerts_per_day: number
  precision: number
  real_in_new: number
  new_cases: number
  overlap_rate: number
  txn_matches: number
  case_days_total: number
}

export type RuleProvenance = "hand_written" | "agent_proposed"

export type RuleEntry = {
  rule: RuleDsl
  provenance: RuleProvenance
  enabled: boolean
  backtest?: Backtest
  source_report_ids?: string[]
  saved_at?: string
}

// ---- SSE event unions, per endpoint ----

export type ToolEvent = { type: "tool"; name: string; label: string; args: unknown }
export type ToolDoneEvent = { type: "tool_done"; name: string; is_error: boolean }
export type FilingEvent = { type: "filing"; label: string }
export type StreamErrorEvent = { type: "error"; message: string }

export type InvestigateEvent =
  | ToolEvent
  | ToolDoneEvent
  | FilingEvent
  | StreamErrorEvent
  | { type: "done"; case: Case; truth: { real: boolean | null } }

export type AskEvent =
  | ToolEvent
  | ToolDoneEvent
  | StreamErrorEvent
  | { type: "done"; answer: string }

export type ReviewEvent =
  | { type: "start"; n: number }
  | {
      type: "reflect_start"
      case_id: string
      old: { recommendation: Recommendation; confidence: number }
      label: HumanLabel
    }
  | { type: "reflect_delta"; case_id: string; text: string }
  | { type: "reflect_done"; case_id: string }
  | { type: "synthesizing"; label: string }
  | { type: "done"; review: ReviewResult }
  | StreamErrorEvent

export type RerunEvent =
  | ToolEvent
  | ToolDoneEvent
  | FilingEvent
  | StreamErrorEvent
  | {
      type: "done"
      old_case: Case
      new_case: Case
      diff: RerunDiff
      outcome: RerunOutcome
      attempt: number
      data_ceiling: boolean
    }

export type ProposeEvent =
  | { type: "resolving"; label: string }
  | { type: "resolved"; n_txns: number }
  | { type: "proposing"; label: string }
  | { type: "rule_draft"; rule: RuleDsl }
  | { type: "revising"; label: string; problems: string[] }
  | { type: "backtesting"; label: string }
  | { type: "done"; rule: RuleDsl; backtest: Backtest; report_id: string }
  | StreamErrorEvent

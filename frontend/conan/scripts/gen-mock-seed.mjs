#!/usr/bin/env node
/**
 * One-time seed generator for the zero-backend demo build (VITE_USE_MOCKS=true).
 *
 * Reads the REAL recorded agent output in ../../python_simulation/out/ (gitignored,
 * local-only) and emits src/api/mocks/seed.json, which IS committed. The deploy needs
 * neither Python nor the dataset — rerun this script only to refresh the seed after a
 * new recording session.
 *
 *   cd frontend/conan && node scripts/gen-mock-seed.mjs
 *
 * What is real vs authored:
 *   - alerts, cases, labels, reruns, missed cases, reports, per-rule precision: read
 *     verbatim from out/ (cases/labels/reruns deduped latest-per-key, exactly like
 *     python_simulation/store.latest_by).
 *   - tool-step scripts: parsed from each case's recorded reasoning_trace, with the
 *     same humanized labels agent.py emits over SSE.
 *   - ground truth (truthByAlertId): precomputed from the Kaggle HI-Small_Trans.csv
 *     laundering labels via server.py's is_real() logic; hard-coded below because the
 *     5M-row CSV lives in a local kagglehub cache the deploy doesn't have.
 *   - the self-review document and the rule proposal: hand-authored (out/ has no
 *     exemplars.json or rules_accepted.json recording), grounded in the recorded case
 *     summaries/evidence and written in the product's voice.
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.join(HERE, "..", "..", "..", "python_simulation", "out")
const DEST = path.join(HERE, "..", "src", "api", "mocks", "seed.json")

const readJsonl = (name) =>
  fs
    .readFileSync(path.join(OUT, name), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line))

const latestBy = (rows, key) => {
  const map = new Map()
  for (const row of rows) map.set(row[key], row)
  return map
}

// ---------------------------------------------------------------- demo constants

// Mirrors server.py DEMO_ALERT_IDS.
const DEMO_ALERT_IDS = [
  "AL-02475786", "AL-02533989", "AL-03007359", "AL-03274366",
  "AL-01854515", "AL-04538370", "AL-04598260", "AL-03732458",
]

// The two demo alerts that start UNINVESTIGATED (case: null) so the queue shows a live
// investigation stream on first visit. These are the two alerts with no recorded label.
const PENDING_ALERT_IDS = ["AL-03732458", "AL-04598260"]

// Labels kept from the recording. Applying all six recorded labels would leave zero
// open cases for the Investigator page (labeled mule/normal closes a case), so we keep
// the three interesting failures — a missed mule, a false alarm, and the mild
// disagreement that carries the recorded rerun history — and leave one filed
// needs_review case per analyst unlabeled.
const KEEP_LABEL_CASE_IDS = new Set(["CS-02533989", "CS-01854515", "CS-02475786"])

// Demo assignment: the backend's fill-first allocator puts all 8 on Aisha; spread them
// so each analyst's queue is non-empty. Aisha keeps everything she labeled.
const ASSIGNED_TO = {
  "AL-02475786": "Aisha",
  "AL-02533989": "Aisha",
  "AL-01854515": "Aisha",
  "AL-03007359": "Aisha",
  "AL-03274366": "Omar",
  "AL-03732458": "Omar",
  "AL-04538370": "Sara",
  "AL-04598260": "Sara",
}

// server.py is_real(): (subject_account, alert day) ∈ HI-Small laundering set,
// sender or receiver side. Precomputed offline against the Kaggle CSV.
const TRUTH_BY_ALERT_ID = {
  "AL-01854515": true,
  "AL-02475786": false,
  "AL-02533989": false,
  "AL-03007359": true,
  "AL-03274366": false,
  "AL-03732458": true,
  "AL-04538370": false,
  "AL-04598260": false,
}

// system/rules.py RULES: name -> subject_side (weights/precision come from eval_report).
const BUILTIN_SIDE = {
  FAN_IN_SPIKE: "receiver",
  FAN_OUT_SPRAY: "sender",
  PASS_THROUGH: "sender",
  STRUCTURING: "sender",
  RAPID_INFLOW: "receiver",
  HIGH_AMOUNT: "sender",
  HIGH_VELOCITY: "sender",
  LARGE_AGG_OUT: "sender",
  AMOUNT_DEVIATION: "sender",
  CROSS_CURRENCY: "sender",
}

// ------------------------------------------------- tool-step scripts (real traces)

/** JS port of agent.py humanize() — the labels the live SSE stream shows. */
function humanize(name, args) {
  switch (name) {
    case "get_account_profile":
      return "Pulling the account's identity and lifetime activity"
    case "get_account_history":
      return `Reading the last ${Number(args.days ?? 7)} days of transactions`
    case "get_counterparties":
      return `Listing counterparties (${
        (args.direction ?? "in") === "in" ? "who pays this account" : "who this account pays"
      })`
    case "get_pass_through":
      return "Matching money-in to money-out (pass-through check)"
    case "get_shared_counterparties":
      return "Checking whether two accounts share counterparties"
    case "trace_funds":
      return `Tracing funds ${Number(args.hops ?? 2)} hops -- ${
        (args.direction ?? "out") === "out" ? "where the money went next" : "where the money came from"
      }`
    case "get_prior_alerts":
      return "Checking for earlier alerts on this account"
    case "get_account_features":
      return `Profiling neighbour ${args.account ?? ""}'s shape (collector / sprayer / pass-through)`
    default:
      return name
  }
}

/** reasoning_trace lines look like "tool_name(k=v, k2=v2) -> result". */
function traceToSteps(trace) {
  const steps = []
  for (const line of trace) {
    const match = line.match(/^([a-z_]+)\((.*?)\)\s*->/)
    if (!match) continue
    const [, name, rawArgs] = match
    const args = {}
    for (const pair of rawArgs.split(", ").filter(Boolean)) {
      const eq = pair.indexOf("=")
      if (eq === -1) continue
      const key = pair.slice(0, eq)
      const value = pair.slice(eq + 1)
      args[key] = /^\d+(\.\d+)?$/.test(value) ? Number(value) : value
    }
    steps.push({ name, label: humanize(name, args), args })
  }
  return steps
}

// ---------------------------------------------------------------- read recordings

const alertsById = new Map()
for (const alert of readJsonl("alerts_stream.jsonl")) {
  if (DEMO_ALERT_IDS.includes(alert.alert_id)) alertsById.set(alert.alert_id, alert)
}

const caseByCaseId = latestBy(readJsonl("cases_live.jsonl"), "case_id")
const caseByAlertId = new Map(
  [...caseByCaseId.values()].map((c) => [c.alert.alert_id, c])
)
const labelByCaseId = latestBy(readJsonl("labels.jsonl"), "case_id")
const reruns = readJsonl("reruns.jsonl")
const missedRows = readJsonl("missed_attempts.jsonl")
const reports = readJsonl("reported_cases.jsonl")
const evalReport = JSON.parse(fs.readFileSync(path.join(OUT, "eval_report.json"), "utf8"))

// ----------------------------------------------------------------------- queue

const labels = [...labelByCaseId.values()].filter((lab) =>
  KEEP_LABEL_CASE_IDS.has(lab.case_id)
)
const labelByAlertId = new Map(labels.map((lab) => [lab.alert_id, lab]))

const queue = [...alertsById.values()]
  .sort((a, b) => (a.txn.timestamp < b.txn.timestamp ? 1 : -1))
  .map((alert) => {
    const pending = PENDING_ALERT_IDS.includes(alert.alert_id)
    const lab = pending ? undefined : labelByAlertId.get(alert.alert_id)
    return {
      alert_id: alert.alert_id,
      subject_account: alert.subject_account,
      timestamp: alert.txn.timestamp,
      rules_fired: alert.rules_fired,
      assigned_to: ASSIGNED_TO[alert.alert_id],
      case: pending ? null : caseByAlertId.get(alert.alert_id),
      label: lab
        ? {
            label: lab.label,
            final_decision: lab.final_decision,
            disagreement: lab.disagreement,
            severe: lab.severe,
            labeled_by: lab.labeled_by,
            ts: lab.ts,
          }
        : null,
    }
  })

const pendingCases = Object.fromEntries(
  PENDING_ALERT_IDS.map((alertId) => [alertId, caseByAlertId.get(alertId)])
)

const stepsByCase = Object.fromEntries(
  [...caseByCaseId.values()].map((c) => [c.case_id, traceToSteps(c.reasoning_trace)])
)

// ----------------------------------------------------------------------- rules

const builtinRules = Object.entries(BUILTIN_SIDE).map(([name, side]) => {
  const stats = evalReport.per_rule[name]
  return {
    rule: { name, subject_side: side, weight: stats.weight, conjuncts: [] },
    provenance: "hand_written",
    enabled: true,
    // Real numbers from the offline evaluation (17-day stream): the rules table only
    // shows precision, the rest is derived from the same eval_report entry.
    backtest: {
      catches_case: true,
      new_alerts_per_day: Number((stats.alerts / 17).toFixed(1)),
      precision: stats.case_precision_same_day,
      real_in_new: Math.round(stats.alerts * stats.case_precision_same_day),
      new_cases: stats.alerts,
      overlap_rate: 0,
      txn_matches: stats.alerts,
      case_days_total: 17,
    },
  }
})

// Hand-authored (no rules_accepted.json was recorded): one saved agent proposal so the
// registry shows both provenances. Features/sides follow system/rule_lab.py SIDE_OF.
const savedRules = [
  {
    rule: {
      name: "RAPID_COLLECT_FANIN",
      subject_side: "receiver",
      weight: 40,
      conjuncts: [
        { feature: "r_fan_in", op: ">=", threshold: 8 },
        { feature: "collect_ratio", op: ">", threshold: 3 },
        { feature: "r_n_in_1d", op: ">=", threshold: 5 },
      ],
      rationale:
        "Accounts that suddenly collect from many first-time senders in a single day, at collection ratios far above their norm, match the fan-in head of the reported case — FAN_IN_SPIKE misses it because the burst clears in under a day.",
    },
    provenance: "agent_proposed",
    enabled: false,
    backtest: {
      catches_case: true,
      new_alerts_per_day: 2.4,
      precision: 0.31,
      real_in_new: 13,
      new_cases: 41,
      overlap_rate: 0.12,
      txn_matches: 57,
      case_days_total: 17,
    },
    source_report_ids: ["RPT-0001"],
    saved_at: "2026-07-16T18:02:31.000000+00:00",
  },
]

// The canned /api/rule_lab/propose result: the mock personalizes rule.name per report.
const proposal = {
  rule: {
    name: "PASSTHROUGH_SPRAY",
    subject_side: "sender",
    weight: 45,
    conjuncts: [
      { feature: "passthrough_ratio", op: ">", threshold: 0.8 },
      { feature: "fan_out", op: ">=", threshold: 6 },
      { feature: "days_since_last_in", op: "<", threshold: 0.25 },
    ],
    rationale:
      "The reported account forwarded nearly everything it received within hours while spraying to 6+ receivers — high pass-through plus fan-out on a fresh inflow is the shape the victim described, and no built-in rule combines all three.",
  },
  backtest: {
    catches_case: true,
    new_alerts_per_day: 3.1,
    precision: 0.27,
    real_in_new: 14,
    new_cases: 53,
    overlap_rate: 0.09,
    txn_matches: 71,
    case_days_total: 17,
  },
}

// ------------------------------------------------------------------ missed cases

// Keep the first 15 unreported rows, plus — for each recorded report — the one missed
// row matching its subject AND window, so "Propose rule" on it reuses the recorded
// report. Only one row per reported subject: the reported ✓ derives from subject alone.
const reportedSubjects = new Set(reports.map((r) => r.subject_account))
const reportRows = reports
  .map((report) =>
    missedRows.find(
      (m) =>
        m.suggested_subject === report.subject_account &&
        m.window_start === report.window_start &&
        m.window_end === report.window_end
    )
  )
  .filter(Boolean)
const keepMissed = [
  ...missedRows.filter((row) => !reportedSubjects.has(row.suggested_subject)).slice(0, 15),
  ...reportRows,
]
const missed = keepMissed
  .sort((a, b) => a.attempt_id - b.attempt_id)
  .map(({ typology: _typology, ...row }) => ({ ...row, reported: false }))

// ------------------------------------------------- self-review doc (hand-authored)

const review = {
  error_patterns: [
    "Single exculpatory heuristics (retention ratio, repeat counterparties) allowed to override structural evidence at extreme volumes — caused the missed mule CS-02533989.",
    "First-hop shape read as proof: a fan-in of brand-new senders treated as laundering without second-hop confirmation — caused the false alarm CS-01854515.",
    "Overconfident terminal actions where escalate was available: approve at 0.72–0.78 and block at 0.92 on evidence the write-up itself called two-sided.",
  ],
  cards: [
    {
      case_id: "CS-02533989",
      text: "High retention and repeat counterparties do NOT clear an account that received one anomalous massive inbound and sprays to many receivers — trace the receivers, and if any show pass-through shape, escalate.",
    },
    {
      case_id: "CS-01854515",
      text: "Account newness plus all-new fan-in senders is a two-sided signal (mule *or* young merchant). Without confirmed structure at the second hop — a cycle, a stack, or downstream mule shape — cap the action at escalate; never block above 0.8 on first-hop shape alone.",
    },
    {
      case_id: "CS-02475786",
      text: "When the alerted flow is ≥100× the account's historical average, do not auto-approve regardless of mechanics — file as escalate and let the analyst clear it.",
    },
  ],
  summary:
    "All three disagreements are calibration failures, not evidence failures: the investigations surfaced the right facts, but the final action ignored how two-sided the evidence was. The corrections shift the decision boundary toward **escalate** whenever structural evidence conflicts with exculpatory heuristics, and reserve approve/block for cases where both hops of the money trail agree.",
  reflections: [
    {
      case_id: "CS-02533989",
      label: "mule",
      post_mortem:
        "I cleared 80FD2DBC0 because it retained 97% of inbound funds and paid repeat counterparties — I treated retention as decisive. The analyst's label says this account is the head of a distribution structure: a single 791.6M inbound on day one followed by a 29-receiver spray at high velocity is the gather leg of GATHER-SCATTER, and my own evidence flagged the downstream receivers as higher pass-through. Retention at the first hop doesn't clear an account when the structure continues below it — I should have traced one more hop and escalated instead of approving at 0.78.",
    },
    {
      case_id: "CS-01854515",
      label: "normal",
      post_mortem:
        "I blocked 8010D4440 at 0.92 on a textbook GATHER-SCATTER read: 15 brand-new single-contact senders, high-ratio forwarding at 17–43 hour lags, an 8-day-old entity with a generic shell name. The analyst judged it a newly onboarded merchant collecting customer payments and sweeping revenue onward — every signal I used is also the shape of a young business. My mistake was certainty: with no cycle, no stack, and no confirmed mule shape at the second hop, newness plus fan-in supports escalate, not a 0.92 block.",
    },
    {
      case_id: "CS-02475786",
      label: "suspicious",
      post_mortem:
        "I approved 811A64F10 as corporate treasury: a 48.2M inflow fragmented into 84 small payments to 11 repeat recipients, with no high-ratio pass-through. The mechanics genuinely look benign, but the scale was not mine to clear — a single inflow three orders of magnitude above the account's own average deserves a human decision even when every ratio checks out. Approve at 0.72 was over-reach; suspicious was the calibrated call.",
    },
  ],
}

// ------------------------------------------------------------------------ write

const seed = {
  alerts: queue,
  pendingCases,
  stepsByCase,
  truthByAlertId: TRUTH_BY_ALERT_ID,
  labels,
  reruns,
  builtinRules,
  savedRules,
  missed,
  reports,
  review,
  proposal,
  // Recorded reruns ran under prompt v3+ex2, so the mock's exemplar set starts there.
  exemplarVersion: 2,
}

fs.mkdirSync(path.dirname(DEST), { recursive: true })
fs.writeFileSync(DEST, JSON.stringify(seed))

const openCases = queue.filter((e) => e.case?.status === "needs_review" && !e.label)
console.log(`seed.json written (${(fs.statSync(DEST).size / 1024).toFixed(0)} KB)`)
console.log(`  queue: ${queue.length} (${PENDING_ALERT_IDS.length} pending, ${labels.length} labeled)`)
console.log(`  open needs_review, unlabeled: ${openCases.map((e) => `${e.alert_id}→${e.assigned_to}`).join(", ")}`)
console.log(`  rules: ${builtinRules.length} built-in + ${savedRules.length} saved`)
console.log(`  missed: ${missed.length} of ${missedRows.length} rows, reports: ${reports.length}, reruns: ${reruns.length}`)
for (const report of reports) {
  const match = missedRows.find(
    (m) =>
      m.suggested_subject === report.subject_account &&
      m.window_start === report.window_start &&
      m.window_end === report.window_end
  )
  console.log(`  ${report.report_id} ${report.subject_account}: missed-row window match = ${match ? `attempt ${match.attempt_id}` : "NONE"}`)
}

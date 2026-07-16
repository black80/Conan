# Feature Requirements — Investigation Loop v2

Refined requirements + user stories for the three new feature areas, reconciled
against the existing `python_simulation/` engine and the Conan FastAPI backend.
Drafted independently per feature, then adversarially cross-reviewed; every
contradiction the review found is resolved in §1 (read §1 first — it is the
contract everything else obeys).

Setting: hackathon, 1–2 days build time. Rule of the house: **derive
aggressively, decorate honestly, fabricate never.** Every number shown to a
judge is measured, or labeled ILLUSTRATIVE.

---

## 0. The loop (why these three features are one feature)

```
                 ┌──────────────────────────────────────────────┐
                 ▼                                              │
  txn stream → RULE ENGINE → Alert → AGENT → Case → INVESTIGATOR
                 ▲                                      │
                 │            LABEL (mule/normal/suspicious) + agent rec snapshot
                 │                                      │
                 │            severe disagreement ──→ F2 MODEL TUNING
                 │            (agent approved a mule /  exemplars + τ re-sweep
                 │             blocked a normal)        → new prompt/threshold → AGENT
                 │
                 │   VICTIM REPORT — fraud that never alerted; the victim calls the
                 │   bank, an investigator FILES it (account + window + description)
                 │            │
                 │            └──→ F3 RULE LAB: system resolves the account's real
                 │                 txns/features → agent proposes rule → backtest on
                 └────────────────  5M txns → human accepts → rules_accepted.json
```

**Artifact handoffs (the loop's load-bearing edges):**

| Edge | Artifact | Producer | Consumer |
|---|---|---|---|
| F1 → F2 | `out/labels.jsonl` rows where failure is **severe** (see D10) | label endpoint | failure harvest |
| intake → F3 | `reported_cases` rows (victim reports, filed by an investigator) | report intake endpoint | Rule Lab seeds |
| F2 → F1 | `agent/exemplars.json` + τ in agent config | exemplar builder | `investigate()` |
| F3 → detection | `out/rules_accepted.json` | Rule Lab save | `run_system.py` |
| (demo prep only) | `out/missed_attempts.jsonl` — which accounts genuinely went un-alerted | `eval/missed.py` | demo operator (tells them which victim reports to file) |

If an edge above is not built, we do not claim the loop closes — we demo the
edges that exist.

---

## 1. Canonical decisions (resolves all cross-draft contradictions)

**D1 — One label taxonomy, stored once.** The human judgment is
`label ∈ {mule, normal, suspicious}` in the `case_labels` table (append-only).
Everything else is *derived*:

| human label | agrees with agent rec | derived `final_decision` | case status after |
|---|---|---|---|
| `mule` | `block` | `confirmed_fraud` | closed |
| `normal` | `approve` | `false_positive` | closed |
| `suspicious` | `escalate` | `escalated` | stays open, priority ↑ |

`disagreement = (label, rec)` not on the diagonal of this table.
`final_decision` is **never written directly** by any feature. Feature 2 reads
`case_labels` (label + snapshot fields), not `final_decision`.

**D2 — Failure harvest fires on label write, not case close.** "Suspicious"
keeps a case open; harvesting on `case.closed` would starve F2 of the
highest-value disagreement (human-suspicious vs agent-approve). The
`POST /cases/{id}/label` handler emits the failure record synchronously.

**D3 — Files are the source of truth for agent behavior; Postgres mirrors for
display.** The agent runs from `python_simulation/` files:
`agent/exemplars.json` (F2 exemplars), τ in the agent config (F2 threshold),
`out/rules_accepted.json` (F3 rules — what `run_system.py` actually loads).
Postgres rows (`PromptVersion`-like records, `FraudRule`) are dashboards, not
inputs. One direction of sync: file → DB, never DB → file.

**D4 — Server ownership.** Everything that runs the agent or the batch engine
is **Flask `server.py`** (it owns the loaded graph + SSE): `/api/cases/{id}/ask`,
all `/rule_lab/*` (including upload), `/tuning/rerun`. Everything that is CRUD
persistence is **Conan FastAPI**: alerts, labels, failures list, rules mirror.
No route exists on both.

**D5 — Rerun/report keys are `case_id` everywhere.** (`alert_id` appears inside
payloads, never as the route key.)

**D6 — Typology fields name their source.** The agent's `Case.typology` is
displayed as *agent-asserted*. Patterns.txt typology appears only in offline
eval views (F3 missed list, benchmark scoring), never inside exemplars or any
agent input.

**D7 — No label-laundering into the agent.** Exemplars (F2) are built **only
from genuinely human-labeled cases** (real clicks in the UI — demo-day clicks
count). Ground-truth-seeded "simulated analyst labels" are banned from the
exemplar path: disclosure does not fix a labels→prompt leak. If too few human
labels exist by demo time, we demo the mechanism on 1–3 real labeled failures
instead of inflating with fake ones. Leave-one-out masking: a case never sees
its own exemplar; benchmark cases are excluded from exemplars (no
train-on-test).

**D8 — Metrics discipline.** Before/after claims quote auto-close purity,
recall, specificity on the frozen benchmark (`eval/benchmark_ids.json`,
n=100). n<100 deltas carry an explicit small-n warning (this project proved
n=30 is ±13pt noise). Any number in a slide or UI that hasn't been measured
yet is labeled ILLUSTRATIVE.

**D9 — Missed cases are victim reports, not a labels diff.** In the product, a
missed case enters when an investigator files a victim's fraud report (subject
account + time window + description); the system then resolves that account's
*actual* transactions and feature rows from the stream. `eval/missed.py`
(Patterns.txt vs alerts diff) is a **demo-prep helper only** — it tells the
operator which accounts genuinely went un-alerted so the reports filed on
stage correspond to real missed laundering. It is not a product path and its
labels never reach the agent.

**D10 — Only severe failures feed tuning.** A tuning failure is
`missed_mule` (agent approved, human labeled mule) or `false_alarm` (agent
blocked, human labeled normal). Mild disagreements (escalate mismatches) are
listed as disagreements in the UI but are **not** harvested into exemplars or
the τ re-sweep.

---

## 2. Feature 1 — Fraud detection flow extensions

**Why (judge pitch).** The alert→agent→case flow already runs end-to-end on 5M
replayed transactions. This closes the loop around the human: cases arrive
assigned and finished, the investigator can interrogate the agent live with
the same as-of-pinned graph tools it investigated with, and every label
captures the agent's recommendation at that moment — turning daily work into
the training set for F2 and F3. Nothing mocked: chat runs the real agent loop.

### User stories

**US-1.1 (Must)** — As an investigator, I want every alert to arrive as a
finished case (summary, evidence ledger, typology, recommendation, confidence),
so that I review work instead of doing it.
- Given the engine emits an alert, when the pipeline runs, then an `alerts`
  row, an `InvestigationCase`, and an `AIRecommendation` exist, linked by
  `alert_id`.
- Given the agent files, when I open the case, then I see the two-sided
  evidence ledger and reasoning trace verbatim from the frozen `Case` contract.

**US-1.2 (Must)** — As an investigator, I want to label a case
**mule / normal / suspicious**, so that my judgment is captured once.
- Given an open case, when I POST a label, then a `case_labels` row stores the
  label **and** `agent_recommendation_at_label` + `agent_confidence_at_label`
  + `prompt_version`, snapshotted at write time.
- Given the label, then `final_decision` and case status derive from table D1;
  the taxonomy is written nowhere else; the row is appended to
  `out/labels.jsonl` and, on disagreement, a failure record is emitted (D2).

**US-1.3 (Must)** — As an investigator, I want to ask the agent follow-up
questions about a case, so that I can probe evidence without querying the
graph myself.
- Given a filed case, when I ask "did funds exit via new counterparties?",
  then the real agent loop (seeded with alert + Case + reasoning_trace) streams
  `tool` / `tool_done` / `done` SSE events and answers citing tool outputs only.
- Given a question about ground truth, then the agent cannot answer (labels
  unreachable by construction) and says so.
- Chat is **ephemeral** in the MVP (lives in the browser session; persistence
  is a Should — see cuts).

**US-1.4 (Must)** — As a team lead, I want cases auto-assigned to the
least-loaded investigator with <10 open cases, so that the queue balances
itself.
- Given a new case, when the allocator runs, then it picks min(open_count)
  under cap 10, tie-break lowest id; if all full, `assigned_to` stays NULL
  (unassigned queue).
- Manual `POST /cases/{id}/assign` always wins; the allocator never overwrites
  a non-null `assigned_to`.
- **Deterministic, not an LLM**: assignment is exact constraint satisfaction;
  an LLM adds latency, cost, nondeterminism, and no auditable answer to "why
  did this case go to X?" — a `GROUP BY assigned_to` is the audit trail.
- Backfill-on-close (freed capacity pulls oldest unassigned case): **Should**.

**US-1.5 (Should)** — As a data scientist, I want the label export
`out/labels.jsonl` (`{case_id, alert_id, label, agent_recommendation,
agent_confidence, prompt_version, labeled_by, ts}`), so that F2/F3 have their
feed. (This is the F1→F2/F3 edge; it ships with US-1.2, not separately.)

### API

| Method | Path | Owner | Request | Response |
|---|---|---|---|---|
| GET | `/alerts?status=&limit=` | FastAPI (new) | filters | `[{alert_id, subject_account, score, rules_fired, status, case_id}]` |
| GET | `/alerts/{alert_id}` | FastAPI (new) | — | frozen Alert JSON + `case_id` |
| POST | `/cases/{id}/label` | FastAPI (new) | `{label, note}` | `{label, agent_recommendation_at_label, disagreement}` |
| POST | `/api/cases/{id}/ask` | Flask (new) | `{question}` | SSE: `tool`/`tool_done` … `{done, answer}` |

Existing `/cases/{id}/assign` and `/close` are kept; close optionally gains
backfill (Should). `PATCH /alerts` — cut (see §5).

### Data model deltas
- FastAPI: new `alerts` (frozen-contract mirror: `alert_id` PK,
  `subject_account`, `subject_side`, `score`, `rules_fired` JSONB, `features`
  JSONB, `status`); new `case_labels` (append-only; fields per US-1.2);
  `Investigator.max_open_cases INT DEFAULT 10`;
  `InvestigationCase.alert_id` FK.
- python_simulation: `contracts.py` Decision gains `label`; `out/labels.jsonl`.

### Agent/LLM design (chat)
Same Haiku + v3 tool rules; system prompt seeded with Alert + filed Case +
reasoning_trace; same 8 read-only tools, every call as-of pinned to
`alert.txn.timestamp`; ≤5 tool calls per question; answers cite tool evidence
and distinguish "from my filed investigation" vs "newly retrieved"; must say
"cannot determine from available data" when true; may not mutate the filed
Case — relabeling is the human's job.

### Demo moment
Open an escalated case → type "trace where the money went after the alert" →
watch `trace_funds(hops=2)` fire live in the SSE stream → agent answers with
real amounts → click **mule** → toast: "label recorded; agent said ESCALATE →
disagreement logged for tuning."

---

## 3. Feature 2 — Model tuning loop ("learn from overrides")

**Why (judge pitch).** Every analyst override is a free label; today it dies in
a column. This turns overrides into *prompt-level learning*: labeled failures
become contrastive few-shot exemplars in the evidence-ledger prompt, the
auto-close confidence gate is re-swept on accumulated labels, and every failure
is genuinely re-run — flips, regressions, and the unfixable data-ceiling
residue are all shown. Honest framing on stage: **this is not fine-tuning**;
it is the two levers you can actually pull on an API model, measured on a
frozen benchmark.

### User stories

**US-2.1 (Must)** — As an analyst, I want every case where the agent was
**severely** wrong collected automatically (D10).
- Given a label write producing a severe failure (D2 timing): `missed_mule`
  (agent approved, human said mule) or `false_alarm` (agent blocked, human
  said normal), then a failure record exists with the agent's Case fields
  snapshotted. Mild disagreements are listed but not harvested.
- Given `GET /tuning/failures`, then I see old recommendation, confidence,
  human label, and agent-asserted typology per case (D6).

**US-2.2 (Must)** — As a model owner, I want selected failures injected as
contrastive exemplars and the confidence gate re-tuned.
- Given ≥1 human-labeled failure (D7), when I `POST /tuning/build`, then up to
  k=4 exemplar cards (≤150 tokens each, ≤800 total) are compiled into
  `agent/exemplars.json` with a version hash, and `eval/sweep.py` re-picks τ
  subject to auto-close purity ≥95%.
- Selection policy: highest-confidence-wrong first; max one per agent-asserted
  typology; benchmark cases excluded; twice-failed cases excluded (they go to
  the data-ceiling registry, not the prompt).
- Card template (facts visible to an analyst only): *"A case with
  [fan_in=12, fs_in ratio 0.9, 48h burst] looked legitimate because
  [exculpatory ledger lines] — the analyst confirmed mule; the tell was [X].
  Patterns, not rules — weigh the current ledger."*

**US-2.3 (Must)** — As a demo viewer, I want each failure re-run through the
real `investigate()` with the new prompt and shown as **old report vs new
report**, so that learning is demonstrated, not asserted.
- Given a failure, when `POST /tuning/rerun/{case_id}` (Flask) runs, then real
  tool calls stream over SSE and the result renders as a side-by-side report
  diff: old recommendation/confidence/evidence ledger vs new, with outcome
  `flipped_correct | still_wrong | flipped_wrong`. The old→new report
  comparison is the centerpiece — it shows *how* the reasoning changed, not
  just that the verdict flipped.
- Given `still_wrong` twice, then the case is stamped `data_ceiling=true` and
  displayed as unfixable — never hidden. (Naming the unfixable subset is the
  credibility move: it is the measured data-ceiling finding.)
- Rerun results are persisted so the diff is viewable later
  (`GET /tuning/reruns/{case_id}`), and the failures list shows each case's
  rerun status — list page → click into any case → old-vs-new diff.

**US-2.4 (Could — pre-run offline, don't build live)** — Before/after metrics
on the frozen n=100 benchmark via `run_agent.py --prompt-version v3.1` +
`eval/triage.py`; served by `GET /tuning/metrics` with `small_n_warning`
per D8. Pre-run once before the demo; never run live on stage.

### API

| Method | Path | Owner | Response |
|---|---|---|---|
| GET | `/tuning/failures` | FastAPI (new) | `[{case_id, old_recommendation, old_confidence, human_label, failure_type, rerun_status, data_ceiling}]` — the list view |
| POST | `/tuning/build` | Flask (new — writes the files, D3) | `{version, exemplar_ids, token_count, tau_old, tau_new}` |
| POST | `/tuning/rerun/{case_id}` | Flask (new, SSE) | events … `{done, old_case, new_case, diff, outcome}` |
| GET | `/tuning/reruns/{case_id}` | FastAPI (new) | stored old-vs-new report diff — the per-case view |
| GET | `/tuning/metrics?version=` | FastAPI (new) | `{before, after, n, small_n_warning}` |

### Data model deltas
- FastAPI: `model_failures` table (mirror for the dashboard; source of truth is
  the labels feed): `case_id` FK, snapshot fields, `failure_type`,
  `used_as_exemplar`, `data_ceiling`. `AIRecommendation.prompt_version`.
- python_simulation: `agent/exemplars.json` (versioned, hashed),
  `eval/benchmark_ids.json` (frozen n=100 set), `Case._meta.exemplars_hash`.
  (No `PromptVersion`/`CaseRerun` tables — files + mirror rows suffice, D3.)

### The "learning animation" — training-style presentation, honest mechanism
The look is a **training sequence** (per product direction): training icons,
staged progress bars, "learning…" loading states — abstract and flashy is
fine. The honesty constraint is on what the stages *are*, not how they look:
every stage in the progress sequence is a real step actually running —
`gathering failures → building exemplars → re-tuning threshold →
re-reviewing cases` — and the timeline advances when the real step completes,
not on a timer. The payoff screen is the **old report vs new report**
side-by-side (verdict, confidence, evidence ledger) showing how the learning
changed the agent's review of the same case; `still_wrong` cases render a red
**DATA CEILING** stamp instead of a flip. Benchmark needles move only if the
offline n=100 re-run was actually done (else "not yet measured"). One
guardrail: the UI copy says "learning" / "adapting", never "training the
model's weights" or "fine-tuning".

### Demo moment
Click **Learn from mistakes** → cards slide into the prompt, τ ticks to its
re-swept value → one case re-runs live and flips APPROVE→ESCALATE → one other
card stamps **UNFIXABLE — data ceiling** and stays red. Learning *and* its
limits, in 30 seconds, all real.

---

## 4. Feature 3 — Agent rule building ("Rule Lab")

**Why (judge pitch).** Some fraud never alerts — the bank finds out when the
victim calls. Rule Lab turns that phone call into detection: an investigator
files the victim's report, the system pulls the reported account's *actual*
transaction history and feature profile, the agent proposes a rule in the same
constrained shape as the existing 10, and the batch engine backtests it over
all 5M transactions in seconds — including the honest tradeoff ("catches the
reported case + N similar, +M alerts/day at P% precision" vs "catches it at
+40k/day — rejected"). Every claim machine-verified, none model-asserted.

### User stories

**US-3.1 (Must)** — As an investigator, I want to **file a victim's fraud
report** for fraud that never alerted, so that the system can learn from what
it missed. *(This is the "upload cases" ask — it's an intake form, not a CSV.)*
- Given a victim's call, when I file a report (subject account, time window,
  free-text description), then a `reported_cases` row is created with
  `status=new`.
- Given the report, then the system resolves the account's actual transactions
  and feature rows from the stream for that window and attaches them — the
  investigator types an account and dates, not feature values.
- Given a filed report, then it appears as a Rule Lab seed. (Demo prep:
  `eval/missed.py` tells the operator which accounts genuinely went un-alerted,
  so reports filed on stage correspond to real missed laundering — D9.)

**US-3.2 (Must)** — As an analyst, I want the agent to propose a candidate rule
from a filed report.
- Given a selected seed, when I click "Propose rule", then the agent returns
  rule JSON in the constrained DSL — conjunction (max 3) of
  `(feature ∈ FEATURE_KEYS, op ∈ {>, <, >=, <=}, threshold)` + `weight` +
  `subject_side` — and a validator rejects unknown features or thresholds
  outside the [q50, q99.99] population band **before anything runs**. The
  model never writes executable code.
- Agent input: contrast pack — the seed's feature rows vs population quantiles
  (q50/q90/q99/q99.9), plus the existing 10 rules (to avoid duplicates), plus
  the DSL grammar and accept criteria.

**US-3.3 (Must)** — As an analyst, I want every candidate backtested on the
full 17-day stream.
- Given a validated candidate, when the backtest runs
  (`system/engine.py --cache`, seconds), then I see: reported case(s) caught
  (case-level), incremental alerts/day, incremental case precision, Jaccard
  overlap with existing rules' alert sets. **The backtest report card is the
  proof** — no live replay theater needed.
- Verdict = **ACCEPT** iff catches ≥1 reported case AND ≤100 incremental
  alerts/day AND incremental case precision ≥5% (~50× base rate) AND overlap
  <0.8. Rejected candidates are displayed with their real (bad) backtests —
  that contrast *is* the demo.
- Review is **accept/reject as-proposed** — no threshold editing UI (a
  rejected rule can be re-proposed; editing is post-hackathon).

**US-3.4 (Should)** — As a team lead, I want accepted rules saved with
provenance, disabled by default, human-gated.
- On save: appended to `out/rules_accepted.json` (authoritative, D3) and
  mirrored to `FraudRule` (`enabled=false`, `provenance='agent_proposed'`,
  `source_report_ids`, `backtest_report`); the source report flips to
  `status=rule_created`.
- On enable: `run_system.py` re-runs the report's window; the previously
  missed alert appears in the queue.

### API (agent/engine routes on Flask, persistence on FastAPI — D4)

| Method | Path | Owner | Response |
|---|---|---|---|
| POST | `/reported_cases` | FastAPI (new) | file a victim report `{subject_account, window_start, window_end, description}` → `{report_id, status:new}` |
| GET | `/reported_cases` | FastAPI (new) | `[{report_id, subject_account, window, status: new\|proposed\|rule_created}]` |
| POST | `/rule_lab/propose` | Flask (new, SSE) | body `{report_id}`; resolves txns/features, then `rule_draft{rule}` → `backtest{…}` → `done{verdict}` |
| POST | `/rule_lab/backtest` | Flask (new) | `{caught_reported, incr_alerts_per_day, incr_case_precision, overlap_jaccard, verdict}` |
| GET | `/rules` | FastAPI (new) | FraudRule mirror + provenance + backtest_report |
| POST | `/rules` | FastAPI (new) | mirror row, `enabled=false` |

### Data model deltas
- FastAPI: new `reported_cases` (`report_id` PK, `subject_account`,
  `window_start`, `window_end`, `description`, `reported_by` FK,
  `resolved_txn_count`, `status ENUM(new, proposed, rule_created)`).
- FastAPI `FraudRule` adds: `provenance ENUM('hand_written','agent_proposed')`,
  `definition JSONB` (the DSL), `weight INT`, `subject_side`,
  `source_report_ids JSONB`, `backtest_report JSONB`.
- python_simulation: `RuleDSL` in `contracts.py`
  (`{name, subject_side, weight, conjuncts:[{feature, op, threshold}],
  provenance}`); `compile_dsl()` in `system/rules.py`;
  `out/rules_accepted.json`; `eval/missed.py` + `out/missed_attempts.jsonl`
  (demo prep only, D9).

### Honest limits (say them on the slide)
The DSL expresses rolling-feature rules (fan-in/out, first-seen inflow, burst,
pass-through). It cannot express graph-traversal typologies (CYCLE needs
multi-hop state) — demo on fan/gather/first-seen patterns and state the limit.
Thresholds snap to the quantile grid and backtest across all 17 days with
non-seed-day precision reported, to resist overfitting the seed.

### Demo moment
Investigator files a victim report — account, two dates, one sentence — the
system instantly attaches the account's real transactions → click "Propose
rule" → agent proposes `FS_INFLOW_BURST: r_fs_in_cnt_1d ≥ 8 ∧ r_burst_in ≥ 6`
(receiver, weight 40) → 5-second live backtest bar → report card: "catches the
reported case + similar ones, +N alerts/day at P% precision — ACCEPTED", next
to a rejected candidate's terrible tradeoff → save → the rule exists with the
victim report as its provenance.
*(All numbers ILLUSTRATIVE until the backtest actually runs.)*

---

## 5. Scope ladder & build order (1–2 days)

**Critical path: Feature 1 labels — F2 and F3 both starve without them.**

**MVP core (build in this order):**
1. F1: alerts API + case→label endpoint (+ `labels.jsonl` export) + capacity
   allocator + ask-agent SSE chat (ephemeral).
2. F3: victim-report intake + resolve txns/features + propose (DSL + validator)
   + real backtest. (F3 before F2 because its seeds don't depend on
   accumulated labels — a report can be filed the moment intake exists.)
3. F2: failures list + exemplar build + **one** live rerun with the old-vs-new
   report diff + the training-sequence presentation.

**Cut from MVP (Should/Could):** chat persistence table; `PATCH /alerts`;
backfill-on-close; F2 n=100 benchmark re-run live (pre-run offline once);
`PromptVersion`/`CaseRerun` tables (files instead); FastAPI enable route
(toggle via the JSON file); propose-revise iteration; rule editing before save
(accept/reject only).

**Out of scope entirely:** auth/RBAC, fine-tuning/LoRA, continuous
auto-retraining, embedding-retrieved exemplars, LLM-based case routing,
editing filed Cases, cross-case agent memory, notifications, non-HI-Small
upload schemas, RabbitMQ wiring for the new flows (direct HTTP between Flask
and FastAPI for the demo; the event topics remain the production shape).

**New plumbing with named owners (unowned = unbuilt):**

| Component | What | Owner |
|---|---|---|
| Alert ingest bridge | replay script POSTs alerts → FastAPI `alerts` | backend person |
| Report resolver | filed report → account's real txns + feature rows for the window | sim person |
| `eval/missed.py` | Patterns.txt vs alerts diff — **demo prep only** (which reports to file, D9) | sim person |
| `compile_dsl()` + validator | DSL JSON → predicate in `system/rules.py` | sim person |
| Backtest metrics | incremental precision / Jaccard / non-report-day split (new code around `engine.py`) | sim person |
| Exemplar builder + τ apply | `agent/exemplars.json`, agent config | sim person |
| Training-sequence UI | staged progress bound to real steps + old-vs-new report diff view | frontend person |
| SSE concurrency guard | cap concurrent Flask agent runs (chat + rerun + backtest) at 3 | sim person |

**Demo-day plan (honest):** the demo operator labels 3–5 cases live in the UI
before/at the start of the demo — real human labels, feeding F2 legitimately —
and files 1–2 victim reports (on accounts `eval/missed.py` says genuinely went
un-alerted) to seed F3. No ground-truth-seeded fake labels (D7).

---

## 6. Honesty guardrails (apply to every feature)

1. The investigation agent never sees `Is Laundering`, `Patterns.txt`, or any
   label — including via exemplars (D7's human-label-only rule + leave-one-out).
2. Human labels feed exactly one learning path: the F2 exemplar/threshold
   pipeline. F3 is seeded by victim reports (human-filed facts, not labels).
   Ground-truth labels (`Is Laundering`/Patterns.txt) appear only in offline
   `eval/` scoring and demo prep (D9).
3. Every metric on screen is produced by `eval/` code or the backtest engine —
   never by the LLM, never hand-typed. Unmeasured numbers say ILLUSTRATIVE.
4. The dataset is synthetic (IBM HI-Small) — disclosed on the slide and in the UI.
5. "Model tuning" is described as prompt + threshold adaptation, never as
   fine-tuning/training.
6. Failures that don't flip are shown, stamped DATA CEILING — the measured
   finding that the transaction graph, not the model, is the bottleneck.

# CLAUDE.md

Read `docs/HANDOFF.md` for full detail (dataset decisions, bugs already fixed, contracts).
Read `docs/RESULTS.md` for real metrics and tuning decisions already made — **do not
re-tread the dead-ends documented there.**
This file is the 60-second version.

---

## The idea

An **AI fraud analyst** for anti-money-laundering.

Today a bank's transaction monitoring system flags thousands of alerts a day. Each one
lands on a human analyst's desk as a single row of a spreadsheet. The analyst then spends
20 minutes pulling up the account's history, listing its counterparties, checking where
the money went next, and writing a case file — before deciding block / approve / escalate.
Most alerts turn out to be nothing.

**We automate the 20 minutes, not the decision.**

Every transaction passes a fast rule engine. Every alert it raises is handed to an agent,
which investigates the account the way a human would — traversing the transaction graph
with tools, gathering evidence, and writing a complete case file with a recommendation,
a confidence, and a full reasoning trace. The analyst opens a finished case, not a raw row.

The agent auto-closes the obvious false positives and escalates the rest with the work
already done.

## Why this needs an agent (and not just a bigger model)

The laundering patterns in our data are **graph structures**: fan-in (many accounts feed
one mule), fan-out (one account sprays many), stack (money passes through a chain), cycle
(money returns to its origin), scatter-gather. None of these are visible in the alerted
row. They are only visible by **traversing to neighbours and following the money N hops**.

A rule engine cannot do that — it sees one transaction at a time.
A classifier cannot explain it — it outputs a number.
An agent with graph tools can do both: find the structure *and* narrate it.

That's the product.

## Architecture

```
                              ┌──────────────────┐
   Transactions ─────────────▶│   RULE ENGINE    │  sync · <100ms · thresholds only
   (HI-Small replay,          │  10 weighted     │◀──── Feature Store
    5M txns / 17 days)        │  rules           │      (rolling account state,
                              └────────┬─────────┘       as-of, no leakage)
                                       │
                                    [ Alert ]  ◀── frozen JSON contract
                                       │
                              ┌────────▼─────────┐
                              │      AGENT       │  async · seconds · 5-10 tool calls
                              │  investigate()   │◀──── Context Tools
                              │                  │      get_account_history
                              │  · gathers       │      get_counterparties
                              │  · reasons       │      get_pass_through
                              │  · names the     │      trace_funds(hops=N)  ◀── the one
                              │    typology      │      get_shared_counterparties  a rule
                              │  · recommends    │      get_account_profile       engine
                              └────────┬─────────┘                              can't do
                                       │
                                    [ Case ]  ◀── frozen JSON contract
                                       │
                              ┌────────▼─────────┐
                              │    ANALYST UI    │  queue · case detail · graph view
                              │  block/approve/  │
                              │  escalate        │
                              └────────┬─────────┘
                                       │
                                   [ Decision ] ──▶ logged as labels (feedback loop)
```

**The seam that matters is sync vs async**, not frontend vs backend. The rule engine sits
inline in the payment path: milliseconds, precomputed features only. The agent runs after:
seconds, ten tool calls, full graph access. That split is what justifies having two layers.

## The three subsystems

Three people, three owners, two contracts between them.

| | Owns | Ships | Depends on |
|---|---|---|---|
| **A — Detection** | rules, feature store, read-only query helpers | `detect(txn) -> Alert \| None` + `get_*()` | nothing |
| **B — Agent** | tool definitions, agent loop, case construction | `investigate(alert) -> Case` | A's query helpers |
| **C — Case UI** | queue, case detail, decision capture | `GET /cases`, `POST /cases/{id}/decision` | nothing (reads `Case`) |

**Why the context tools live in A's box, not B's:** they are read queries against A's
database and schema. A is already writing those queries for the feature store. B wraps
them as tools and decides *when* to call them. Put them in B and you get a merge conflict
every hour.

**How to work in parallel:** freeze `Alert` and `Case` (see `HANDOFF.md` §5 and
`contracts.py`), then each person writes their own fixture JSON on day zero and codes
against *that*, not against anyone's running service. Wire the real calls in at the end.
Integrating early means debugging each other's half-finished code all day.

## The one rule that governs everything

> **The rule engine is a sampler, not a detector.**

Its only job is to hand the agent ~500 interesting cases (~30/day — a human-sized queue).
We optimise for **alert volume**, **lift over base rate**, and **typology coverage**.

We do **not** optimise F1. Published SOTA on this dataset is ~60% minority-class F1 using
a graph neural network. We would lose that race, and it is not the race we're in. If the
rules were 95% precise, the agent would be decoration — the noisy alert stream is what
gives it a job.

## Repo layout — one module per pipeline stage

Entry points (run with `python <file>.py`) live at the root; the layers they
drive are packages; the analysis/verification scripts are grouped under `eval/`
(run with `python -m eval.<name>` from the root).

```
set_dependencies.py  SETUP: install deps, fetch data, build artifacts (run first;
                     idempotent; `--check` reports readiness without changing anything)
run_system.py        ENTRY POINT: replay txns -> daily alert queues (the demo)
run_agent.py         ENTRY POINT: alerts -> agent -> out/cases.jsonl (needs ANTHROPIC_API_KEY)
server.py            ENTRY POINT: live UI backend — runs the REAL agent, streams it (SSE)
simulate.py          ENTRY POINT: CLI day-replay of the alert stream
contracts.py         frozen Alert/Case/Decision shapes (the team's interfaces)
system/              the pipeline: txn -> feature store -> rules -> sampler -> Alert
  feature_store.py     per-account rolling state (in RAM) + update() per txn
  rules.py             THE 10 RULES as plain predicate functions + weights/quota
  detector.py          glue: detect(store, txn, thr) -> candidate | None (~20 lines)
  sampler.py           daily promotion: subject-dedup + typology quota -> Alert JSON
  engine.py            batch twin: calibration + metrics (python -m system.engine)
agent/
  agent.py             investigate(alert) -> Case: Claude + tool loop, as-of pinned
  tools.py             the 8 read-only graph tools the agent calls
eval/                scorecards & verification (python -m eval.<name>)
  triage.py            proof #1 scorecard: agent triage vs ground truth
  sweep.py             offline threshold/confidence sweep over an existing run
  parity.py            proves batch ≡ stream — run after ANY rule change
  escalate_retest.py   one-off study: does get_account_features fix escalates?
ui/                  frontend served by server.py
  index.html           the live UI (SSE-driven; what server.py serves)
  replay.html          self-contained offline demo (no server / no API key)
fixtures/            real Alert JSONs + example Case (B & C code against these)
  make_fixtures.py     regenerates them from out/alerts.parquet
docs/                HANDOFF.md (design), RESULTS.md (real metrics), architecture.drawio
out/                 generated, gitignored: alerts_stream.jsonl (the agent's
                     inbox), alerts.parquet, eval_report.json (thresholds)
data/                rejected candidate datasets from selection (unused; deletable)
```

## Status (2026-07-14)

- ✅ **The base system is live**: `python run_system.py <Trans.csv> [Patterns.txt]`
  replays transactions in time order through the streaming detector (µs per txn)
  and promotes a daily queue → `out/alerts_stream.jsonl` — **the agent's inbox.**
  Default budget is **500/day (agent-scale)**: 5,369 alerts, 16.5% case precision
  (10.3% in the realistic first-10-days regime — always disclose the split),
  **8/8 typologies and 206/370 labeled attempts covered in the realistic regime
  alone**. `--daily-budget 30` is the human-scale option: 54.6% case precision
  (33% realistic) but coverage collapses to 4/8. Both points in `docs/RESULTS.md`.
  Quote CASE-level numbers, never txn-level (wrong metric — the agent
  investigates accounts, not rows).
- ✅ `contracts.py` — frozen. One extension over HANDOFF §5: `Alert.subject_account` /
  `subject_side` = the account the rules indict. **The agent investigates
  `subject_account`, not `txn.sender_account`.**
- ✅ `agent/agent.py` — the investigation agent (Claude Opus 4.8, manual tool
  loop, every tool call pinned to as-of alert time; labels unreachable by
  construction). `run_agent.py` batches it over the queue with resume;
  `python -m eval.triage` scores triage vs ground truth.
- ✅ Live frontend: `server.py` runs the REAL agent per alert and streams each
  tool call to `ui/index.html` over Server-Sent Events (Haiku v3, 8-alert demo).
  `ui/replay.html` is the offline, no-server fallback.

## Data

IBM AML **HI-Small** (`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`).
5,078,345 transactions · 17 days · 5,177 laundering (0.102%) · account-to-account with
`Payment Format` covering wire, ACH, credit card, cheque, cash.

It ships `Patterns.txt` — ground-truth labels for all 8 laundering typologies. **Hide the
typology from the agent, then score whether it names the pattern correctly.** *"The agent
independently identified the laundering typology in N of M cases"* is a stronger demo than
any precision number, and it's free.

Never let the agent see `Is Laundering` or `Patterns.txt`.

## Environment

- Python: `/opt/miniconda3/envs/waleed/bin/python` (polars 1.29, pydantic 2.10).
- Data: `~/.cache/kagglehub/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8/`.
- Batch engine run: ~35s, ~5 GB peak. Streaming replay: ~95s, ~6.5 GB peak.
  Run from a terminal, never a notebook kernel.
  Use `--cache <features.parquet>` — threshold/config sweeps then take seconds.
- Feature store emits 40+ features per txn (volumes 24h/7d, first-seen inflow,
  pair stats, recency, ratios) — the full list is `FEATURE_KEYS` in
  `system/rules.py`; parity across both engines enforced by `eval/parity.py`
  (41 compared columns).

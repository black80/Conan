# Fraud/AML Agent — Handoff

> **2026-07-14: numbers and status in this doc are superseded.** The engine has since
> been run and tuned on real HI-Small — see `RESULTS.md` for real metrics, design
> changes (subject_account, typology-quota sampler), and tuning dead-ends. This doc
> remains the reference for dataset rationale, the 7 fixed bugs (§4), and contracts (§5).

Hackathon project. Status as of this document: **rule engine written and debugged, but
NOT yet validated on real data.** Agent and frontend not started.

---

## 1. What we're building

A three-layer AML system:

```
Transactions ──▶ Rule Engine ──▶ Alert ──▶ Agent ──▶ Case ──▶ Analyst UI ──▶ Decision
                 (sync, fast)              (async, tools)      (review + act)
```

**The core thesis:** rules are a *sampler*, not a detector. Their only job is to hand
the agent a small, high-yield stream of cases to investigate. The agent is the product;
the rules are plumbing.

This matters because it changes the optimisation target. We do **not** care about F1.
We care about:
- **alert volume** (~500 total, ~30/day — a human-sized queue)
- **lift over base rate** (how much richer than random are the alerts we ship?)
- **typology coverage** (does the agent see at least one of each laundering pattern?)

Do not "improve" the rule engine by chasing F1. Published SOTA on this dataset is
~60% minority-class F1 with a graph neural net. We will lose that race and it is not
the race we're in.

### Work split (3 people)

| Owner | Subsystem | Ships |
|---|---|---|
| A | Detection: rules + feature store + read-only query helpers | `detect()`, `get_*()` |
| B | Agent: tools, agent loop, case construction | `investigate(alert) -> Case` |
| C | Case API + analyst frontend | `GET /cases`, `POST /cases/{id}/decision` |

The seams are two frozen JSON contracts (`Alert`, `Case`). Everyone mocks the others'
output and works against fixtures. Do not integrate early.

---

## 2. Dataset: IBM AML HI-Small

**Kaggle:** `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`
Use **HI-Small** (`HI-Small_Trans.csv`, `HI-Small_Accounts.csv`, `HI-Small_Patterns.txt`).

### Why this one

We evaluated six datasets. The selection criterion was **not** "which detects fraud
best" — it was **"does an agent have anything to investigate?"** An agent needs an
*entity graph*: columns that reference persistent things (accounts, devices) that
**recur** across rows, so the agent can traverse to neighbours and pull history.

| Dataset | Verdict |
|---|---|
| **IBM AML HI-Small** | ✅ **CHOSEN.** Real account graph (multi-agent simulator). POS *and* transfers via `Payment Format`. Ships an entity table + ground-truth typology labels. |
| `aryan208/financial-transactions` | ❌ Entity keys are noise. `device_hash`: 3,835,723 unique / 5M rows (1.3/key). `ip_address`: 4,997,068 / 5M (**1.0006/key** — that's birthday-paradox collisions, i.e. `faker.ipv4()` called per row). `sender_account`: 5.6/key. Schema *looks* rich; the graph is decoration. |
| PaySim | ❌ Sender-receiver pairs are all unique — no graph. Also `amount == oldbalanceOrg` is a near-perfect detector, so rules would trivially win and the agent has no job. Balance fields are known-leaky (cancelled txns). |
| `mlg-ulb/creditcardfraud` | ❌ The only *real* data, and useless for us: PCA'd to `V1…V28`. Agent opens a case and finds `V14 = -3.2`. Nothing to narrate. It's a classifier benchmark. |
| Sparkov (`kartik2112`) | ❌ Card-only (fails the POS+transfers requirement). Also: `merch_lat/long` is jitter around the customer's home (corr 0.994 / 0.999) so there is no geo signal; and **every merchant name is prefixed `fraud_`**, which will poison an LLM agent reading raw rows. |
| £ card CSV | ❌ No entity ID at all. Every row atomic. |

**IBM AML is synthetic.** So is everything else on Kaggle (real transaction-level fraud
data doesn't get published — PII). That's fine. The rule is:

> **Derive aggressively. Decorate honestly. Fabricate never.**
>
> Deriving (fan-in, pass-through, counterparty overlap) = free, zero risk, and it's the
> agent's whole value. Fabricating any column with knowledge of the label manufactures
> the signal and a judge will find the loop.

### Schema — `HI-Small_Trans.csv`

| Column | Notes |
|---|---|
| `Timestamp` | `2022/09/01 00:20` — **minute resolution**, causes massive ties |
| `From Bank` | |
| `Account` | **sender** |
| `To Bank` | |
| `Account` | **receiver — yes, the same column name twice** |
| `Amount Received` | |
| `Receiving Currency` | |
| `Amount Paid` | |
| `Payment Currency` | |
| `Payment Format` | Cheque / Credit Card / ACH / Wire / Cash / Bitcoin / Reinvestment |
| `Is Laundering` | 0/1 — **label, never expose to the agent** |

Stats: **5,078,345 rows · 17 days · 5,177 laundering (0.102%)**

### Gotchas

1. **Duplicate `Account` column.** Polars renames the second to `Account_duplicated_0`;
   pandas gives `Account.1`. `load()` handles this by detecting the dup dynamically.
2. **Minute-resolution timestamps.** Thousands of rows share a timestamp. Any rolling
   window or as-of join keyed on raw `ts` will produce many-to-many matches and explode
   the row count. See Bug #1 below.
3. **`Amount Paid != Amount Received`** on cross-currency transactions. Keep both — the
   mismatch is a layering signal.
4. **Self-transfers exist** (sender == receiver).
5. **`HI-Small_Accounts.csv` is unused so far.** It's the entity/profile table. The agent
   should read it. Nobody has looked at its schema yet — do that.

### `Patterns.txt`

Ground-truth laundering typologies. Format is blocks:

```
BEGIN LAUNDERING ATTEMPT - FAN-IN
<transaction rows>
END LAUNDERING ATTEMPT - FAN-IN
```

Eight typologies: **FAN-IN, FAN-OUT, GATHER-SCATTER, SCATTER-GATHER, CYCLE, RANDOM,
BIPARTITE, STACK**.

This is a gift. Two uses:
- **Validation:** confirm the rules surface accounts from every typology (already wired
  into the `coverage()` report).
- **Free agent eval:** hide the typology from the agent, then score whether its narrative
  independently names the right pattern. *"The agent didn't just flag it — it correctly
  identified the laundering typology in N of M cases"* is a far stronger demo than a
  precision number. **Set this up early.**

Note: RANDOM and BIPARTITE are near-impossible for rules. Expect near-zero coverage on
those. That's expected and fine.

---

## 3. Current state: `rule_engine.py`

Works. Debugged. **Tuned against a synthetic fixture, not against HI-Small.**

### ⚠️ The metrics you may have seen are FIXTURE metrics

I could not reach Kaggle from my sandbox, so I built a stand-in dataset (300k rows,
0.206% base rate, 4 injected typologies) and tuned against it. Results there:

```
budget 500 → 31% precision | 151x lift | 4/4 typologies surfaced
```

**These numbers mean nothing for HI-Small. Do not put them in a slide.** Real HI-Small
has 8 typologies (including two that rules can't catch) and a lower base rate, so expect
materially lower precision — plausibly 5–15%. That is still fine: at 10% precision, 500
alerts = ~50 real cases, which is a working demo.

**FIRST ACTION FOR CLAUDE CODE: run it on the real file and get real numbers.**

```bash
python rule_engine.py \
  ~/.cache/kagglehub/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8/HI-Small_Trans.csv \
  ~/.cache/kagglehub/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8/HI-Small_Patterns.txt \
  --budget 500
```

Peak memory ≈ 4 GB at 5M rows. **Run from a terminal, not a Jupyter kernel** (it OOM'd
the kernel before). `--rows 2000000` if needed.

### Pipeline

```
load()      scan_csv → rename dup Account → parse ts → ts_u = ts + µs(txn_id)
features()  ONE combined event stream (each txn → 2 events: sender-out, receiver-in)
            → expanding distinct counterparties (is_first_distinct + cum_sum)
            → rolling 1d sums/counts
            → hub-invariant ratios
calibrate() thresholds from the ACCOUNT-level q=0.99 distribution
score()     weighted rules → score + rules_fired list
dedup()     one case per (sender_account, day), keep max score
            rank by score, take top --budget
→ alerts.parquet
```

### Feature store

Computed as-of each transaction — **no future leakage**. This frame is also the backend
for the agent's tools.

| Feature | Meaning |
|---|---|
| `fan_in` / `fan_out` | sender's expanding distinct counterparties |
| `r_fan_in` / `r_fan_out` | receiver's, same |
| `collect_ratio` | `r_fan_in / (r_fan_out + 1)` — **hub-invariant** |
| `spray_ratio` | `fan_out / (fan_in + 1)` |
| `n_out_1d`, `amt_out_1d`, `max_out_1d` | sender velocity/volume, 24h |
| `r_n_in_1d` | receiver inbound velocity, 24h |
| `sender_inflow_1d` | sender's *inbound* in 24h (for pass-through) |
| `passthrough_ratio` | `min(inflow, outflow) / max(inflow, outflow)` |
| `sender_avg` | sender's lifetime mean outbound amount |

### The 10 rules

Thresholds marked *calibrated* are derived from the data at `CALIBRATION_Q = 0.99` of the
**account** distribution — they are not hardcoded numbers.

| Rule | Weight | Condition |
|---|---|---|
| `FAN_IN_SPIKE` | **80** | `collect_ratio` > calibrated, floor `r_fan_in >= 5` |
| `FAN_OUT_SPRAY` | **80** | `spray_ratio` > calibrated, floor `fan_out >= 5` |
| `PASS_THROUGH` | **70** | `passthrough_ratio > 0.90` ∧ `sender_inflow_1d > 10k` ∧ `n_out_1d <= 5` |
| `STRUCTURING` | **50** | `n_out_1d >= 5` ∧ `amt_out_1d > 30k` ∧ `max_out_1d < 10k` |
| `RAPID_INFLOW` | 15 | `r_n_in_1d` > calibrated, floor `collect_ratio >= 2` |
| `HIGH_AMOUNT` | 10 | `amount_paid` > calibrated |
| `HIGH_VELOCITY` | 10 | `n_out_1d` > calibrated |
| `LARGE_AGG_OUT` | 10 | `amt_out_1d` > calibrated |
| `AMOUNT_DEVIATION` | 10 | `amount_paid > 10 × sender_avg` |
| `CROSS_CURRENCY` | 10 | `payment_currency != receiving_currency` |

**The weight split is load-bearing.** See Bug #5.

---

## 4. Bugs already fixed — DO NOT REGRESS THESE

Each of these was found the hard way. They are all easy to reintroduce.

### #1 — Non-unique rolling index → join explosion
**Symptom:** 72,848,207 alerts from 5,078,345 transactions.
**Cause:** HI-Small timestamps are minute-resolution. Thousands of rows share a `ts`.
Rolling windows and as-of joins keyed on `ts` matched many-to-many.
**Fix:** `ts_u = ts + duration(microseconds=txn_id)` — unique, monotonic, order-preserving.
Every window and join uses `ts_u` (or `te` in the event frame), never raw `ts`.

### #2 — Per-transaction alerting
**Symptom:** ~1M alerts. One busy mule generated 200 of them.
**Cause:** alerting per transaction. But an analyst investigates an **account**, not each
individual payment.
**Fix:** `dedup()` — one case per `(sender_account, day)`, keep the highest-scoring txn.

### #3 — Thresholds calibrated on the transaction distribution
**Symptom:** `FAN_IN_SPIKE` threshold landed at `fan_in > 866`. Zero mules caught.
**Cause:** the q=0.999 of *transactions* is dominated by hub accounts (banks, exchanges),
because transactions are weighted by activity. Real mules have fan-in ~15 and sit far
below that.
**Fix:** calibrate on the **account-level** distribution: `group_by(account).max()` then
quantile. Hubs are one row each and can't skew it.

### #4 — Raw fan-in level is not discriminative
**Symptom:** even after #3, `FAN_IN_SPIKE` fired 0 times. A threshold low enough to catch
mules also catches every legitimate hub.
**Cause:** the *level* doesn't separate a mule from a bank. Both have high fan-in.
**Fix — the key insight:** a bank **intermediates** (high fan-in AND high fan-out, ratio
≈ 1). A mule **collects** (high fan-in, near-zero fan-out, ratio >> 1). Calibrate on the
**hub-invariant ratio**, not the level, with an absolute floor to avoid firing on an
account with 1 inbound and 0 outbound:

```python
collect_ratio = r_fan_in / (r_fan_out + 1)   # mule >> 1, bank ≈ 1
spray_ratio   = fan_out  / (fan_in + 1)
```

Calibrating on the level finds banks. Calibrating on the ratio finds mules.

### #5 — Correlated generic rules crowd out typology rules
**Symptom:** graph rules fired, but zero mules appeared in the top-500 alerts.
**Cause:** ranking by score. One big busy (legit) account trips `HIGH_AMOUNT` +
`HIGH_VELOCITY` + `LARGE_AGG_OUT` + `AMOUNT_DEVIATION` — four *correlated* rules — and
stacks to 85. A mule tripping only `FAN_IN_SPIKE` scored 40 and fell below the budget cut.
**Fix:** typology rules (80/80/70/50) must outrank **any** stack of generic rules
(10–15 each, ~55 combined). Keep it that way.

### #6 — Mixing two different accounts in one ratio
**Symptom:** precision collapsed to 0% at every budget. Everything tied at the same score.
**Cause:** `collect_ratio` was computed as `receiver.fan_in / sender.fan_out` — *two
different accounts*. Meaningless number.
**Fix:** a single **combined event stream**. Each transaction emits two events
(`dir=1` sender-outbound, `dir=0` receiver-inbound). Cumulative counts are taken
`.over("account")`, so an account's in-fan and out-fan always come from the **same
account**. Each event carries its `txn_id`, so state joins back with a plain hash join.

### #7 — OOM (kernel crash) at 5M rows
**Cause:** `rolling(period="7d").n_unique()` re-scans the window for every row →
effectively quadratic on hub accounts with huge windows.
**Fix:** replaced the 7d rolling distinct count with an **expanding** distinct count
computed in one pass: `pl.struct("account","cp","dir").is_first_distinct()` then
`.cum_sum().over("account")`. Causally correct (no future leakage), and over a 17-day
file an expanding count ≈ a 7d rolling one. Also: lazy scan + streaming collect, Float32
amounts, and two wide `join_asof`s replaced by `txn_id` hash joins.

Peak RSS is now ~1.2 GB at 1.5M rows, scaling ~linearly (≈4 GB at 5M).

---

## 5. Contracts (freeze these before splitting work)

```python
class Transaction(BaseModel):
    txn_id: str
    timestamp: datetime
    sender_bank: str
    sender_account: str
    receiver_bank: str
    receiver_account: str
    amount_paid: float
    payment_currency: str
    amount_received: float
    receiving_currency: str
    payment_format: str          # Wire | ACH | Credit Card | Cheque | Cash | Bitcoin

class Alert(BaseModel):
    alert_id: str
    txn: Transaction
    rules_fired: list[str]       # ["FAN_IN_SPIKE", "PASS_THROUGH"]
    score: int
    features: dict[str, float]   # the feature-store row: fan_in, collect_ratio, ...
    created_at: datetime

class Evidence(BaseModel):
    label: str                   # "Inbound counterparties (7d)"
    value: str                   # "17 distinct senders, 0 outbound"
    supports: Literal["fraud", "legit", "neutral"]

class Case(BaseModel):
    case_id: str
    alert: Alert
    summary: str                 # agent narrative, 3-5 sentences
    evidence: list[Evidence]
    typology: str | None         # agent's guess: FAN-IN / STACK / ... (scored vs Patterns.txt)
    recommendation: Literal["block", "approve", "escalate"]
    confidence: float
    reasoning_trace: list[str]   # every tool call + result. POPULATE FROM COMMIT ONE.
    status: Literal["auto_closed", "needs_review", "resolved"]

class Decision(BaseModel):
    case_id: str
    action: Literal["block", "approve", "escalate"]
    analyst_note: str | None
    agreed_with_agent: bool      # derived, not asked
```

**`is_laundering` is kept in `alerts.parquet` for evaluation only. Strip it before the
agent reads a case.** Same for `typology` from `Patterns.txt` — the agent must infer it,
not be told it.

`reasoning_trace` is the field everyone forgets and the one that makes the demo. It's what
lets a judge see *why* the agent said block. Append to it on every tool call.

---

## 6. Agent tool layer — TO BUILD

HI-Small has **no device, no IP, no geo**. The context layer is *purely the account graph*.
That's fine — it's the strongest angle anyway, and it's what the typologies are made of.

Tools to implement (queries over the transaction table + `Accounts.csv`):

```python
get_account_profile(account) -> dict          # from Accounts.csv + lifetime aggregates
get_account_history(account, days) -> list[Transaction]
get_counterparties(account, direction, days) -> list[(account, n_txns, total_amt)]
get_pass_through(account, window_hours) -> list[(inbound, outbound, lag, ratio)]
get_shared_counterparties(a, b) -> list[str]  # do two accounts overlap?
trace_funds(account, hops, direction) -> graph  # follow the money N hops — this is the
                                                # one a rule engine CANNOT do
get_prior_alerts(account) -> list[Alert]
```

`trace_funds` is the money shot. It's what distinguishes an agent from a threshold, and
it's how the agent can name STACK / CYCLE / SCATTER-GATHER typologies that no single-row
rule can see.

---

## 7. Frontend user stories — TO BUILD

Priority order (cut from the bottom if time runs out):

1. Analyst opens a case, reads the agent's summary **first** (verdict before evidence)
2. Sees the evidence table, colour-coded by `supports` (fraud / legit / neutral)
3. Approves / blocks / escalates in one click; lands on the next case
4. Queue sorted by score, filterable by status, with counts
5. Expands the `reasoning_trace` — which tools the agent called, what came back
6. Sees the transaction **graph** around the account (fan-in/out visual) — biggest lift,
   biggest effort
7. (Demo-only) A separate tab of `auto_closed` cases with the agent's written rationale,
   proving it handles the easy ones alone

Cut: auth, multi-analyst assignment, audit log, search.

---

## 8. Immediate next steps

1. **Run `rule_engine.py` on real HI-Small.** Get real precision / lift / typology
   coverage. Everything downstream is blocked on this.
2. **Tune** `CALIBRATION_Q` (0.99) and `--budget` (500) until the alert stream is
   human-sized and has enough true positives that the agent has real cases to crack.
   Target: ≥10% of shipped alerts contain laundering.
3. **Inspect `HI-Small_Accounts.csv`** — nobody has looked at its schema. It's the agent's
   profile table.
4. **Freeze the contracts** (section 5) and generate fixture `Alert` / `Case` JSON so
   Person B and Person C can start immediately without waiting on each other.
5. Build the tool layer (section 6), then the agent, then the UI.

## 9. Open decisions

- **Rail 2 (card/POS).** `Payment Format` includes Credit Card, so both rails are already
  in one schema. Decide whether card transactions get their own rule pack or share the
  graph rules. Recommendation: ship the transfer/graph rail properly first.
- **Sync vs async agent.** Queued is easier to demo (UI shows a populated backlog without
  waiting on live agent runs). Recommendation: queued.
- **Auto-close.** Both easy and hard cases should produce a `Case` and land in the same
  queue; the only difference is `status`. One field, not one branch — cheaper to build and
  the demo still shows the agent closing easy cases on its own.

## 10. Files

| File | Status |
|---|---|
| `rule_engine.py` | ✅ Written, debugged, fixture-tuned. **Not yet run on real data.** |
| `contracts.py` | ❌ Not written — section 5 above |
| `tools.py` | ❌ Not written — section 6 above |
| `agent.py` | ❌ Not written |
| frontend | ❌ Not written |
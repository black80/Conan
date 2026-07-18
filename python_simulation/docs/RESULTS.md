# Rule engine — real HI-Small results (2026-07-14)

The handoff's fixture numbers are obsolete. These are real, from the full
5,078,345-row HI-Small file. Reproduce with:

```bash
python -m system.engine HI-Small_Trans.csv HI-Small_Patterns.txt --budget 500
# ~40s, ~3.6 GB peak RSS. Run from a terminal, not a notebook kernel.
```

---

## AGENT TRIAGE — live results (2026-07-15, Claude Haiku 4.5)

Proof #1: can the agent auto-close false alerts while keeping the real ones?
Measured on a **balanced 8-real / 8-false probe** (labels used only to pick the
set, never shown to the agent; `python run_agent.py --balanced 8`). Two prompt
versions on the SAME 16 cases:

| metric | v1 (naive prompt) | v3 (evidence-ledger prompt) |
|---|---|---|
| recall — real cases kept | 50% (4/8) | **87.5% (7/8)** |
| specificity — false cleared | 12.5% (1/8) | **50% (4/8)** |
| effect on queue | 0.7x (harmful) | **1.3x richer, 1.5x smaller** |
| typology named right | 0/5 | 1/5 |

v3 confusion matrix:
```
                approve  escalate  block
  real (8)         1        6        1     -> 7/8 real kept (recall 87.5%)
  false (8)        4        1        3     -> 4/8 noise auto-closed
```

**v1 failed (near chance).** Not for lack of investigation — it ran 9-19 tool
calls and wrote coherent case files — but for lack of DISCRIMINATION. It
approved a real mule after seeing a 94.6% pass-through (the textbook layering
signature) by inventing an unverified "EUR->USD FX settlement" story, and
blocked a legit account at 0.95 confidence with a fluent-but-wrong
SCATTER-GATHER narrative. Both case files READ well; that is the trap.

**v3 fixed it via prior-art prompt patterns** (from arXiv 2604.19755 evidence
ledger, RiskTagger 2510.17848 fixed-dimension rubric, calibration/abstention
papers). The load-bearing changes:
- **Two-sided evidence ledger** — the model must record CONTRADICTING /
  exculpatory facts, not only incriminating ones. A narrative-first pass drops
  the "this looks legit" column; that omission caused BOTH failure modes.
- **BLOCK gated behind a completed trace_funds/pass_through** — never on the
  alerted row or raw volume alone.
- **ESCALATE as the explicit uncertainty sink** — visible in the output: the
  escalates carry low confidence (0.50-0.76), the blocks 0.92. Haiku now hedges
  instead of guessing, so it wrongly auto-closed only 1 of 8 real cases (v1: 4).

Caveats: n=16 (8+8) is small — the direction is unmistakable but the exact
percentages are noisy. The 44% escalate rate is the SAFE failure mode for a
weak model; a stronger model should convert more escalates into confident
approves (that gap = proof #4, the model-tier ablation, still to run on the
same probe). Typology naming stays weak on Haiku (1/5).

### Controlled A/B: prompt v3 vs v4 (2026-07-15, Haiku, paired n=30)

Before spending on a stronger model, we validated a prompt redesign with a
controlled experiment: **model fixed (Haiku 4.5), toolset fixed (all 8),
same 30 balanced samples (15 real + 15 false, hash-selected -> byte-identical
alerts through both arms), same ~4,800-char investigation prefix.** The ONLY
variable is the decision mechanism:
- **v3** — the model picks approve/escalate/block directly.
- **v4** — the model reports calibrated P(legitimate)/P(laundering) + whether
  it traced a structure; the HARNESS applies a threshold (approve if
  P(legit) >= 0.70; block if P(laundering) >= 0.75 AND a trace tool actually
  ran; else escalate). Motivated by arXiv 2601.07767: models produce
  calibrated confidence but do NOT act on it, so the decision must leave the
  model. Run with `run_agent.py --prompt-version {v3,v4}`.

| metric | v3 (model decides) | v4 (harness threshold) |
|---|---|---|
| recall (real kept) | 66.7% (10/15) | **73.3% (11/15)** |
| specificity (false auto-closed) | 33.3% (5/15) | **46.7% (7/15)** |
| real wrongly auto-closed | 5 | **4** |
| queue precision after agent | 50.0% | **57.9%** |
| typology named correctly | 12% (1/8) | **38% (3/8)** |
| wrongful block recs (false->block) | 1 | 6 |

At the default threshold on n=30, v4 edged v3 on every headline metric — BUT
see the correction below: the larger Sonnet run showed this was small-sample
noise. **Do not cite the n=30 A/B as evidence v4 beats v3.**

> **CORRECTION (after the Sonnet n=100, below).** The n=30 A/B "v4 win" did
> not hold up. The probability AUC of v4's `p_legitimate` is ~0.5 (coin flip)
> for BOTH Haiku (0.50) and Sonnet (0.47) -- the model's legitimacy
> probability does not separate real from false at all, so thresholding it
> (v4) has no real signal to work with. v3's *direct decisions* actually
> discriminated better (Haiku v3 n=100: 41% specificity, and 36/38 of its
> approvals were true false-positives). Net: v3 >= v4 on this task; the n=30
> result was within its ~13pt noise band. Lesson: asking the model to DECIDE
> beat asking it for a probability we threshold -- the opposite of what the
> research (arXiv 2601.07767) predicted, because these models' legitimacy
> probabilities are not calibrated/discriminative enough on the hardest
> alerts to threshold.

### Real n=100 deployment test (uniform mix, direct measurement)

The balanced-16 above measures per-class rates; this run measures the actual
queue. 100 alerts sampled UNIFORMLY from the realistic-regime stream (real
~10% mix, as they'd arrive), Haiku v3, `run_agent.py --limit 100`:

```
                approve  escalate  block
  real (14)        2        4        8     -> 12/14 kept = 86% recall
  false (87)      36       34       17     -> 36/87 cleared = 41% specificity
```

**Direct deployment impact:** analyst queue 101 -> 63 cases (38% less work),
precision 13.9% -> 19.0% (this sample ran a bit rich; at the true 10.3% base
rate it normalizes to ~14.4%). 86% of real fraud still reaches a human; 2 of
14 real cases wrongly auto-closed.

The n=100 confirms the n=16 on recall (86% vs 87.5%; pooled 19/22 = 86% over
22 real cases) but corrects specificity DOWN (41% vs the optimistic 50%). So
the honest deployment number on the full 5,000-alert realistic queue:

  **10.3% -> ~14.4% precision, ~38% less analyst workload, ~86% recall,
  ~2,000 false positives auto-closed, ~74 real cases missed.**

Modest precision gain, solid recall, real workload cut. The limiter is
SPECIFICITY (41%). We hypothesized a stronger model would clear more noise and
lift precision toward ~34% -- **the Sonnet n=100 below FALSIFIED that
hypothesis** (Sonnet was no better; both models' legitimacy AUC ~0.5). The
limiter is not model capability, it is the information in the transaction
graph. `python -m eval.triage` prints the workload/precision projection with an
error band from any probe.

Harness notes: `agent/agent.py` runs both Claude (Anthropic API) and
gpt-oss/Groq (OpenAI-compatible) through one loop; `run_agent.py --concurrency
N` runs cases in parallel (11 cases / 142s at 4-way). Prompt caching is OFF on
Haiku (system+tools below its 4096-token cache minimum), so each case costs
~40-60k input tokens (~$0.05-0.07). Groq free tier (8k tokens/min) is too small
for this multi-turn loop — needs the Dev tier.

---

## THE LIVE SYSTEM (this is the product; batch numbers below are its calibration)

`run_system.py` replays the 5M transactions in time order through
`system/feature_store.py` + `system/rules.py` — the streaming twin of the batch engine (identical
semantics, proven by `eval/parity.py`: 0 mismatches over 300k txns × 18
columns) — and promotes a human-sized queue at each day boundary (30/day,
subject-dedup + typology quota). Output: `out/alerts_stream.jsonl`, one frozen
`Alert` JSON per line. That file IS the agent's inbox.

```bash
python run_system.py HI-Small_Trans.csv HI-Small_Patterns.txt --daily-budget 30
# ~90s replay (~55k txn/s; per-txn detect() is microseconds), 443 alerts
python -m eval.parity            # batch/stream parity — run after ANY rule edit
```

**Two operating points, chosen by who reads the queue** (default: 500/day —
the reader is the agent, not a human team):

| daily budget | alerts | real cases | precision (all) | precision (realistic 01-10) | attempts covered, realistic only |
|---|---|---|---|---|---|
| **500 (default, agent-scale)** | 5,369 | 884 | 16.5% | **10.3%** | **206/370, 8/8 typologies** |
| 30 (human-scale) | 443 | 242 | 54.6% | 33.0% | 5/370, 4/8 typologies |

The 30/day queue is precise but narrow — a human team's day. At 500/day the
agent absorbs the volume, precision stays at the >=10% target in the honest
regime, and coverage explodes: RANDOM and CYCLE — unreachable at any small
budget — get covered in the realistic regime. This is the whole thesis in one
table: the cheaper the reader, the wider the sampler can cast.

The per-day budget beats the batch global top-500 because it stratifies over
time: each day ships its local best, so quiet days aren't drowned by busy
days' hubs.

**Disclose the regime split when presenting.** HI-Small's generator winds down
after 09-10: legit traffic collapses (~100 txn/day) while laundering attempts
continue, so days 11-18 run at 58-73% base rate and their 143 alerts are
~100% precision. Split honestly:

| Regime | Alerts | Case precision | Typologies covered |
|---|---|---|---|
| Days 09-01 → 09-10 (99.9% of volume) | 300 | **33.0%** | 4/8 |
| Days 09-11 → 09-18 (wind-down tail) | 143 | ~100% | +4 more -> 8/8 |

Both regimes are legitimately part of the replay, but "33% in the realistic
regime, 54.6% overall" is the claim that survives a judge's follow-up.

**Cooldown (`--cooldown-days`, default off).** Suppressing re-promotion of a
subject sounds right and was measured: it HALVES early-regime alert precision
(33% -> 17.3% at 3d) while finding only 2 more distinct dirty subjects
(29 vs 27) — the "repeat" alerts are dirty accounts genuinely re-offending
daily. Leave it off for the demo; in production repeats should attach to the
existing open case (Person C: case updates, not new cases).

**Feature store enrichment (2026-07-14, after the ARB vocabulary review).**
`Alert.features` now carries 40+ fields per side: 24h AND 7d volumes,
first-seen inflow (`fs_in_cnt/sum` — money from NEW senders, the mule
signature), pair stats (`is_first_seen_pair`, `pair_n_txns`,
`pair_age_days`), recency (`days_since_last_in/out`), and `net_flow`.
Rules, thresholds, and every metric above are UNCHANGED — the enrichment
feeds the agent's case files, not detection. Deliberately skipped as
degenerate on a 17-day, no-pre-history dataset: tenure, dormancy beyond
days-since-last, 30d windows, monthly baselines (they belong in the ARB
repo, which has the history to feed them). Parity now spans 41 columns.
Cost: batch ~35s/5GB, replay ~95s/6.5GB.

## Headline (budget 500, defaults)

| Metric | Value |
|---|---|
| Case precision (alerted account touches laundering that day) | **20.6%** (103/500) |
| Account precision (subject involved in laundering, any day) | **25.4%** |
| Case-level lift over base rate (0.32% of active account-days) | **63x** |
| Typology coverage, subject is in a labeled attempt | **5/8** |
| Typology coverage, any side of an alerted txn | **6/8** |
| Queue size | 500 alerts (~30/day) — human-sized |

Txn-level precision is 1.0% — ignore it, it was always the wrong metric. The
alert's specific transaction is rarely the laundering row, but the *account*
it points at carries laundering the same day in 1 of 5 cases. The agent
investigates accounts, not rows.

Rule-level case precision within the shipped queue: FAN_OUT_SPRAY 39%,
FAN_IN_SPIKE 31%, RAPID_INFLOW 13%, PASS_THROUGH 5%, STRUCTURING 0%.

## Blind spots (documented, not hidden)

CYCLE, RANDOM, GATHER-SCATTER never make a 500-alert budget as subjects.
Honest best ranks of their attempt accounts in the full ranked stream:
GATHER-SCATTER ~509, CYCLE ~1,212, RANDOM ~2,819. The handoff predicted
RANDOM and BIPARTITE were rule-invisible; we actually get BIPARTITE (2
attempts) and lose CYCLE instead. This is the agent's job, not the rules':
`trace_funds` from an adjacent STACK/SCATTER-GATHER alert can walk into
cycles the single-row rules cannot see.

## What changed vs the handoff spec (and why)

1. **`subject_account`** (new Alert field): each rule indicts a side —
   FAN_IN_SPIKE indicts the *receiver*. v1 opened every case on the sender, so
   one hot receiver tripped 22 alerts on 22 innocent payers and FAN-IN
   coverage was 0/40. Dedup now keys on `(subject_account, day)`.
   FAN_IN_SPIKE case precision went 10% -> 31%.
2. **Typology-quota sampler** (`--stratify typology`, default): 50% of the
   budget is round-robined across FAN_IN_SPIKE / FAN_OUT_SPRAY / PASS_THROUGH
   dominant alerts, the rest filled by plain score. Without it, generic-rule
   stacks push legit hubs above real mules (bug #5 at a different altitude)
   and coverage collapses to 3/8. STRUCTURING gets no quota: it maps to no
   HI-Small typology and measured 0% case precision (its 10k/30k USD
   thresholds are meaningless on rupee/yen rows) — it still competes in the
   score fill and stays in the rule pack for real-world credibility.
3. **`burst` features** (`burst_out`, `r_burst_in`): share of lifetime distinct
   counterparties acquired in the last 24h, one-pass rolling sum of the
   existing first-distinct flag. Used as score tie-break; exposed to the agent
   as evidence. NOTE: ranking quota slots primarily by burst was tested and
   REJECTED — it buys +2.4pp precision but loses FAN-IN entirely (labeled
   FAN-IN mules are slow collectors, burst ~0.25). Coverage outranks precision
   per the handoff's own priorities.
4. **PASS_THROUGH excludes self-transfers**: a txn's own inbound leg lands 1us
   before its outbound leg in the event stream, so a lone Reinvestment row
   scored as a perfect pass-through. 5 of 500 shipped alerts were this
   artifact (2 coincidentally covered FAN-OUT/CYCLE attempts — that luck is
   gone from the numbers above, which is why older runs showed 7/8).
5. **f64 aggregation**: rolling sums on 1e10-scale hub accounts silently drop
   1e3-scale txns in f32 (24-bit mantissa). Amounts stored f32, aggregated f64.
6. **Guards**: >30M-row files are rejected (ts_u microsecond offsets would
   spill past the 60s timestamp resolution); the 1:1 join guard raises
   RuntimeError instead of a `-O`-strippable assert.

## Fidelity to the lost original

The original engine (written and tuned in the sandbox) never made it to this
machine; only the handoff doc did. This reconstruction was validated against
the original's `alerts.parquet` (found in the kagglehub dir, 00:54 run):
identical distinct-sender count (402), identical laundering hits (6/500),
same score band — before the changes listed above were applied on top.

Adversarial review (9 agents, one per documented bug + leakage/semantics):
zero critical/major regressions of the 7 handoff bugs. Known accepted
deviation: generics stack to 65 > STRUCTURING's 50 (the handoff's "~55" was
miscounted even for the original 10 weights); accepted because STRUCTURING
is noise on this dataset and the quota guarantees its queue presence anyway.

## Tuning notes (so nobody re-treads)

- `CALIBRATION_Q`: 0.99 is the sweet spot. 0.995+ collapses coverage (2-4/8);
  0.98 loses FAN-IN.
- `--scope account` (one case per account, not per account-day): precision
  crashes 20.6% -> 6.0%. Dirty accounts alerting on multiple days is signal.
- Budget 550/600: dilutes precision, recovers nothing. 500 stands.
- Fan-size caps to exclude "institutional" accounts: catastrophic (3.2%),
  real laundering subjects often have fan > 33. Rejected.
- Account-roster / entity-size / account-prefix filters: rejected — either
  no signal (all accounts are in the roster; entity metadata is decorative)
  or label-derived (the `1`-prefix pattern is only visible through labels).

- `--scope account`, `--rank typology`, `--stratify all` CLI modes: removed
  from the code after losing the sweeps above — the findings live here so the
  knobs don't have to.

## Files

| File | Status |
|---|---|
| `system/rules.py` | ✅ single source of truth: rule names/weights/sides, quota set, `load_thresholds()` |
| `system/feature_store.py` + `system/rules.py` | ✅ streaming core: `detect(txn) -> candidate \| None` + daily sampler; label-free by construction |
| `system/engine.py` | ✅ batch twin: calibration (thresholds), evaluation (`python -m system.engine`) |
| `run_system.py` | ✅ the base system: replay -> daily queues -> `out/alerts_stream.jsonl` (+ metrics when given Patterns.txt) |
| `eval/parity.py` | ✅ batch/stream equivalence check — run after ANY rule change |
| `out/alerts_stream.jsonl` | ✅ 443 Alert-contract JSONs — **the agent's inbox** |
| `out/alerts.parquet` | ✅ batch 500-alert stream (has `is_laundering` — EVAL ONLY, strip before the agent) |
| `out/eval_report.json` | ✅ batch metrics + the calibrated thresholds `run_system.py` loads |
| `contracts.py` | ✅ frozen (adds `subject_account`/`subject_side` to Alert) |
| `fixtures/` | ✅ real Alert JSONs + example Case for Persons B & C (`make_fixtures.py` regenerates) |
| `agent/tools.py` | ✅ written (all 7 section-6 query tools) — NOT yet independently verified; label never loaded into the module |
| `agent/agent.py`, frontend | ❌ not started (Persons B & C) |

### Sonnet n=100 (v4) — the model-tier test, and a data-ceiling result (2026-07-15)

We ran the strongest available model (Claude Sonnet 5) on the v4 prompt,
uniform n=100, to answer: does a stronger model confidently clear the false
positives Haiku couldn't? **It does not.** ~$10 (heavy prompt caching offset
the adaptive-thinking output). 91/100 completed (9 lost to rate-limit
exhaustion).

- Sonnet's `p_legitimate` **AUC = 0.47** (Haiku v4's = 0.50) — both coin flips.
  The probability does not rank legitimate above fraud. Threshold sweep: at NO
  threshold does Sonnet reach Haiku v3's 41%-specificity / 86%-recall point;
  its whole curve sits below (e.g. tau=0.5 -> 29% spec / 83% recall).
- Sonnet was MORE conservative than Haiku: at the default threshold it cleared
  only 18% of false positives (Haiku 41%), dumping most into escalate.

**Interpretation — the bottleneck is INFORMATION, not model capability.**
These alerts are the rule engine's hardest cases by construction; a busy legit
account and a mule look near-identical in the transaction graph. Two frontier
tiers (Haiku, Sonnet) hit the *same* ceiling, so a bigger model cannot help.

**Corrected bottom line for the demo:**
- The agent's proven, defensible value: **~38% analyst workload cut at ~86%
  recall, with a full audit trail and typology naming per case** (Haiku v3,
  n=100). Precision lifts modestly (10.3% -> ~14.4%) and is capped by the data.
- The lever for higher precision is **richer data** (KYC, device/IP, sanctions
  lists — what a real bank has and HI-Small lacks), NOT a bigger model. This is
  the honest, credible story: we found the ceiling and identified it as data.
- Untested cell: Sonnet + v3 (direct decision). The AUC evidence predicts a
  data ceiling regardless, but it's the one 2x2 cell not run (~$10).

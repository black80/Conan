# Backend API Reference

Complete reference for the `python_simulation` Flask backend (`server.py`) —
everything a frontend needs to wire against it. Written from the code as
shipped on `mvp-core`.

- **Base URL**: `http://localhost:8000`
- **Auth**: none (local demo). CORS is enabled for all origins.
- **Content type**: JSON everywhere; streaming endpoints use Server-Sent Events.
- **Errors**: non-2xx responses carry `{"error": "<message>"}` with status
  `400` (bad input) or `404` (unknown id).

Every agent interaction is a **real model call** (Claude Haiku, `claude-haiku-4-5`,
prompt v3 evidence-ledger) against live graph tools pinned to the alert's
timestamp. Ground-truth labels are unreachable from any agent path.

---

## 1. Running it

```bash
python set_dependencies.py        # one-time: deps + dataset + artifacts -> "READY"
python server.py                  # boots in ~30-60s (loads 5M-txn graph, ~3GB RAM)
```

- Requires `ANTHROPIC_API_KEY` — read automatically from `python_simulation/.env`.
- The server prints `ready: 8 alerts, http://localhost:8000` when up. Until
  then all endpoints refuse connections — the frontend should retry `GET /api/alerts`.
- Single process; the transaction graph lives in RAM. Restart = re-load (~30-60s).
- **Concurrency**: live agent runs (investigate / ask / rerun / propose) are
  capped at **3 concurrent** by a server-side semaphore; excess requests queue
  transparently (the SSE just starts later).

**Reset demo state** (fresh day, keeps the engine artifacts):

```bash
rm -f out/cases_live.jsonl out/labels.jsonl out/reported_cases.jsonl \
      out/reruns.jsonl out/rules_accepted.json out/exemplars.json
```

---

## 2. SSE conventions

Streaming endpoints emit standard SSE frames:

```
data: {"type": "...", ...}\n\n
```

- `GET /api/investigate` can be consumed with a plain `EventSource`.
- The **POST** streaming endpoints (`ask`, `review`, `rerun`, `propose`)
  cannot use `EventSource` (it is GET-only). Use `fetch` + stream reading:

```js
async function sseFetch(url, body, onEvent){
  const res = await fetch(url, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  if(!res.ok){ onEvent({type:'error', message:(await res.json()).error}); return; }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n\n')) >= 0){
      const chunk = buf.slice(0, i); buf = buf.slice(i+2);
      const line = chunk.split('\n').find(l=>l.startsWith('data: '));
      if(line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
```

Every stream ends with a terminal event: `{"type":"done", ...}` **or**
`{"type":"error","message"}` — after that the connection closes.

**Shared tool-event vocabulary** (emitted by investigate / ask / rerun while
the agent works):

| event | payload | meaning |
|---|---|---|
| `tool` | `{name, label, args}` | agent started a tool call; `label` is human-readable ("Tracing funds 2 hops — where the money went next") |
| `tool_done` | `{name, is_error}` | that tool call finished |
| `filing` | `{label}` | agent is writing its verdict (investigate/rerun only) |

---

## 3. Data contracts

### Alert (frozen contract, produced by the rule engine)

```json
{
  "alert_id": "AL-01854515",
  "txn": {
    "txn_id": "1854515", "timestamp": "2022-09-07T06:16:00",
    "sender_bank": "…", "sender_account": "8010D4440",
    "receiver_bank": "…", "receiver_account": "80CD41030",
    "amount_paid": 18721.36, "payment_currency": "US Dollar",
    "amount_received": 18721.36, "receiving_currency": "US Dollar",
    "payment_format": "ACH"
  },
  "subject_account": "8010D4440",
  "subject_side": "sender",
  "rules_fired": ["PASS_THROUGH"],
  "score": 70,
  "features": { "fan_in": 3.0, "passthrough_ratio": 0.97, "…": "~40 features" },
  "created_at": "2022-09-08T00:00:00+00:00"
}
```

### Case (the agent's filed report)

```json
{
  "case_id": "CS-01854515",
  "alert": { "…full Alert…" },
  "summary": "3-5 sentence findings…",
  "evidence": [
    {"label": "Pass-through pattern", "value": "94% of inflow left within 4h",
     "supports": "fraud"}
  ],
  "typology": "GATHER-SCATTER",
  "recommendation": "block",
  "confidence": 0.95,
  "reasoning_trace": ["get_account_profile(account=…) -> …", "…"],
  "status": "needs_review",
  "_meta": {
    "model": "claude-haiku-4-5", "prompt_version": "v3",
    "tool_calls": 15, "usage": {"input_tokens": 0, "…": 0},
    "created_at": "…"
  }
}
```

- `evidence[].supports` ∈ `fraud | legit | neutral` — the two-sided ledger.
- `typology` ∈ `FAN-IN, FAN-OUT, GATHER-SCATTER, SCATTER-GATHER, CYCLE,
  STACK, BIPARTITE, RANDOM` or `null`.
- `recommendation` ∈ `approve | escalate | block`;
  `status` = `auto_closed` (approve) or `needs_review`.
- `_meta.prompt_version` becomes `"v3+exN"` when approved correction cards
  were active for that run.

### Label taxonomy (D1)

Human label → derived meaning; **disagreement** = label off this diagonal.

| label | agrees with agent | `final_decision` | case after |
|---|---|---|---|
| `mule` | `block` | `confirmed_fraud` | closed |
| `normal` | `approve` | `false_positive` | closed |
| `suspicious` | `escalate` | `escalated` | stays open |

**Severe failure** (drives learning): agent `approve` + label `mule`
(`missed_mule`), or agent `block` + label `normal` (`false_alarm`).
Everything else off-diagonal is a `mild_disagreement`.

### Rule DSL (Rule Lab)

```json
{
  "name": "RX_COLLECT_SCATTER",
  "subject_side": "receiver",
  "weight": 65,
  "conjuncts": [
    {"feature": "r_fan_in",  "op": ">=", "threshold": 16.0},
    {"feature": "r_fan_out", "op": ">=", "threshold": 17.0}
  ],
  "rationale": "the agent's full written reasoning (5-10 sentences)"
}
```

- `op` ∈ `>, >=, <, <=`; 1–3 conjuncts; `weight` ∈ [10, 100].
- Features are **side-pure** — every conjunct must describe the rule's
  `subject_side`:
  - **receiver-side**: `collect_ratio, r_fan_in, r_fan_out, r_burst_in, r_n_in_1d`
  - **sender-side**: `spray_ratio, passthrough_ratio, fan_in, fan_out,
    burst_out, n_out_1d, days_since_last_out, days_since_last_in`
- The validator additionally requires that at least one of the reported
  case's own transactions satisfies **all** conjuncts jointly.

### Backtest metrics (neutral — no machine verdict)

```json
{
  "catches_case": true,
  "new_alerts_per_day": 0.1,
  "precision": 0.5,
  "real_in_new": 1,
  "new_cases": 2,
  "overlap_rate": 0.0052,
  "txn_matches": 2659,
  "case_days_total": 30
}
```

- `precision` = share of the rule's **new** (not already alerted) case-days
  that touch real laundering. `overlap_rate` = Jaccard overlap with the
  existing rules' alert set. The analyst decides; the API never verdicts.

---

## 4. Endpoints

### 4.1 Queue (F1)

#### `GET /api/alerts`
The whole queue state in one call — use it to render and to restore after
refresh.

Response: array (time-ordered) of

```json
{
  "alert_id": "AL-01854515",
  "subject_account": "8010D4440",
  "timestamp": "2022-09-07T06:16:00",
  "rules_fired": ["PASS_THROUGH"],
  "assigned_to": "Aisha",
  "case": { "…full Case…" } ,
  "label": {"label": "mule", "final_decision": "confirmed_fraud",
            "disagreement": false, "severe": false,
            "labeled_by": "Aisha", "ts": "…"}
}
```

- `case` / `label` are `null` until the alert is investigated / labeled.
- `assigned_to` comes from the deterministic **fill-first** allocator
  (roster `Aisha, Omar, Sara`, cap 10 open cases each; closed cases free
  capacity). It is a pure function of (alerts, labels) — stable across calls.

#### `GET /api/investigate?id=<alert_id>`  — SSE (EventSource-compatible)
Runs one **real investigation**; the finished Case persists server-side.

Events: `tool` / `tool_done` / `filing` …then
`{"type":"done","case":{…Case…},"truth":{"real":true}}`.

- `truth.real` is post-hoc ground truth **for the UI badge only** — revealed
  after the verdict, never to the model.
- If approved correction cards exist (see §4.3) they are automatically active
  (leave-one-out: an alert never sees its own card).
- Typical: 20–90 s, ~$0.03. `404` on unknown id.

#### `POST /api/cases/<case_id>/label`
File the investigator's verdict. **This is the loop's hinge** — disagreement
detection and failure harvest happen here, synchronously.

Request: `{"label": "mule" | "normal" | "suspicious", "note": "optional"}`

Response (the stored record):

```json
{
  "case_id": "CS-01854515", "alert_id": "AL-01854515",
  "label": "mule", "note": "",
  "agent_recommendation": "block", "agent_confidence": 0.95,
  "prompt_version": "v3", "labeled_by": "Aisha",
  "disagreement": false, "severe": false, "failure_type": null,
  "final_decision": "confirmed_fraud", "closes_case": true,
  "ts": "2026-07-16T…+00:00"
}
```

Labels are append-only; re-labeling a case appends a new record and the
**latest label wins** everywhere. `400` bad label, `404` unknown case.

#### `POST /api/cases/<case_id>/ask`  — SSE (use `sseFetch`)
Follow-up chat with the agent about a filed case. The agent answers from its
filed report first and runs **up to 5 live tool calls** when the question
needs new data (still as-of the alert time).

Request:

```json
{"question": "Did the money go to new counterparties?",
 "history": [{"role":"user","content":"…"},{"role":"assistant","content":"…"}]}
```

`history` is the prior turns (client-held; chat is ephemeral server-side).

Events: `tool` / `tool_done` … then `{"type":"done","answer":"…text…"}`.
Typical: 5–30 s, ~$0.01. `400` empty question, `404` unknown case.

---

### 4.2 Model tuning (F2)

#### `GET /api/tuning/failures`
All **disagreements** (deduped: latest label per case), for the tuning table.

```json
[{
  "case_id": "CS-02533989", "alert_id": "AL-02533989",
  "old_recommendation": "approve", "old_confidence": 0.85,
  "human_label": "mule",
  "severe": true, "failure_type": "missed_mule",
  "typology": null,
  "labeled_by": "Aisha",
  "rerun_status": null,
  "data_ceiling": false
}]
```

- `failure_type` ∈ `missed_mule | false_alarm | mild_disagreement`.
- `rerun_status` ∈ `null | flipped_correct | still_wrong | still_correct |
  regressed` (latest verify run).
- `data_ceiling: true` after a case stayed `still_wrong` twice — treat as
  unfixable and exclude from further reruns.

#### `POST /api/tuning/review`  — SSE (use `sseFetch`)
The agent **reviews its own disagreed cases**: streams a first-person
post-mortem per case, then a distilled adaptation proposal. **Installs
nothing** — approval is a separate call.

Events, in order:

| event | payload |
|---|---|
| `start` | `{n}` — number of cases under review |
| `reflect_start` | `{case_id, old:{recommendation, confidence}, label}` |
| `reflect_delta` | `{case_id, text}` — append to that case's post-mortem (live typing) |
| `reflect_done` | `{case_id}` |
| `synthesizing` | `{label}` |
| `done` | `{review}` — see below |

`review`:

```json
{
  "error_patterns": ["Treating upstream corporate legitimacy as prophylactic…", "…"],
  "cards": [{"case_id": "CS-02533989", "text": "≤90-word correction card…"}],
  "summary": "first-person: how my reasoning will change…",
  "reflections": [{"case_id": "…", "label": "…", "post_mortem": "full text"}]
}
```

Typical: ~5 s per case (streamed) + one synthesis call. `400` when there are
no disagreements.

#### `POST /api/tuning/approve`
Install the human-approved adaptation. The correction cards become part of
the agent's system prompt for **every subsequent investigation** (and rerun).

Request: `{"cards":[{case_id,text}…], "error_patterns":[…], "summary":"…"}`
(pass the `review` object's fields straight through).

Response: `{"version": 2, "n_cards": 4}` → the active prompt is now `v3+ex2`.
`400` when `cards` is empty.

#### `POST /api/tuning/rerun/<case_id>`  — SSE (use `sseFetch`)
Genuinely re-investigate one labeled case with the currently installed
cards, and diff old vs new. Use after approval to verify the change
(the frontend loops this over every disagreement).

Events: `tool` / `tool_done` / `filing` … then

```json
{"type": "done",
 "old_case": {…Case…}, "new_case": {…Case…},
 "diff": {"recommendation": ["block","escalate"],
          "confidence": [0.95, 0.62],
          "typology": ["GATHER-SCATTER", null],
          "human_label": "mule"},
 "outcome": "still_wrong",
 "attempt": 1,
 "data_ceiling": false}
```

`outcome` ∈ `flipped_correct` (was wrong → now agrees with the label) ·
`still_wrong` · `still_correct` · `regressed` (was right → now wrong; show it,
never hide it). `400` if the case has no label or no cards are installed;
`404` unknown case. Typical: 20–90 s, ~$0.03.

---

### 4.3 Rule Lab (F3)

#### `GET /api/missed`
Missed cases (fraud with no alert, from offline evaluation) — the victim
reports feed. **Typology is stripped server-side**: these are undiscovered
patterns; neither the UI nor the agent gets the ground-truth shape.

```json
[{
  "attempt_id": 48,
  "suggested_subject": "8075AC7C0",
  "window_start": "2022-09-01", "window_end": "2022-09-09",
  "n_accounts": 17, "accounts": ["…up to 6…"], "n_txns": 28,
  "context": "Victim reports: US Dollar 108,005 arrived in 13 transfer(s) from 13 counterpart(ies); US Dollar 47,307 left in 15 transfer(s) to 15 account(s) over ~7 day(s). No alert was raised at the time.",
  "reported": false
}]
```

`reported: true` once a victim report exists for that subject.

#### `POST /api/reports` · `GET /api/reports`
Victim-report intake (the frontend files one automatically from a missed-case
row, passing the row's `context` as `description`).

POST request:

```json
{"subject_account": "8075AC7C0",
 "window_start": "2022-09-01", "window_end": "2022-09-09",
 "description": "…the victim's story…"}
```

POST response `201`: the report + `n_txns` (the account's real transactions
resolved for the window). GET returns all reports with derived
`status: "new" | "rule_created"`.

#### `POST /api/rule_lab/propose`  — SSE (use `sseFetch`)
The flagship flow: contrast pack → agent drafts a rule → validator (with one
feedback retry) → **real backtest over all 5M transactions**.

Request: `{"report_id": "RPT-0001"}`

Events, in order:

| event | payload |
|---|---|
| `resolving` | `{label}` |
| `resolved` | `{n_txns}` |
| `proposing` | `{label}` |
| `rule_draft` | `{rule}` — Rule DSL incl. verbose `rationale` (may appear **twice** if the validator rejected the first draft) |
| `revising` | `{label, problems:[…]}` — validator feedback retry in progress |
| `backtesting` | `{label}` |
| `done` | `{rule, backtest, report_id}` — backtest = neutral metrics (§3) |
| `error` | `{message}` — e.g. both drafts failed validation |

Typical: 10–30 s total (agent call(s) + seconds-fast backtest), ~$0.01–0.02.

#### `POST /api/rule_lab/backtest`
Re-score any rule without an agent call (e.g. after manual threshold edits).

Request: `{"rule": {…Rule DSL…}, "report_id": "RPT-0001"}` (`report_id`
optional — without it `catches_case` is always `false`).
Response: neutral metrics (§3). `400` with validator problems if the rule is
malformed.

#### `GET /api/rules` · `POST /api/rules`
The rules registry. GET returns built-ins + saved:

```json
[
 {"rule": {"name": "FAN_IN_SPIKE", "weight": 80, "subject_side": "receiver"},
  "provenance": "hand_written", "enabled": true},
 {"rule": {…Rule DSL…}, "backtest": {…}, "provenance": "agent_proposed",
  "enabled": false, "source_report_ids": ["RPT-0004"], "saved_at": "…"}
]
```

POST saves an accepted rule (`{"rule": …, "backtest": …, "report_id": …}`) →
`201`, stored `enabled: false` with the victim report as provenance. The
authoritative store is `out/rules_accepted.json` (what a re-detection run
loads); any DB is a mirror.

---

## 5. Feature flows (endpoint sequences)

**Queue / normal flow**
```
GET /api/alerts                     render queue (+ restore case/label state)
GET /api/investigate?id=…           per alert, staggered -> live SSE -> Case
POST /api/cases/{id}/label          verdict; disagreement harvested here
POST /api/cases/{id}/ask            optional follow-up chat (SSE)
```

**Model tuning**
```
GET  /api/tuning/failures           disagreements table
POST /api/tuning/review             SSE: post-mortems + proposed adaptation
POST /api/tuning/approve            human gate -> cards installed (v3+exN)
POST /api/tuning/rerun/{case_id}    per disagreement: verify, old-vs-new diff
```

**Rule Lab**
```
GET  /api/missed                    missed-cases table (+ context sidebar)
POST /api/reports                   auto-filed from the selected missed case
POST /api/rule_lab/propose          SSE: draft -> validate -> backtest
POST /api/rules                     save on the analyst's decision
```

---

## 6. Persistence (file stores, `out/`, gitignored)

| file | format | written by | read by |
|---|---|---|---|
| `cases_live.jsonl` | append; latest per `case_id` wins | investigate / rerun | alerts, label, ask, tuning |
| `labels.jsonl` | append-only label events | label endpoint | alerts, tuning, allocator |
| `reported_cases.jsonl` | append | reports POST | reports GET, propose |
| `reruns.jsonl` | append | rerun | failures (`rerun_status`, `data_ceiling`) |
| `rules_accepted.json` | JSON list (authoritative) | rules POST | rules GET, missed (`✓`), re-detection |
| `exemplars.json` | JSON doc (the installed adaptation) | approve | investigate, rerun |
| `missed_attempts.jsonl` | offline eval output | `python -m eval.missed` | missed GET |

These files are the system of record (REQUIREMENTS.md D3). A FastAPI/Postgres
backend can mirror them later; sync direction is file → DB only.

## 7. Timing & cost cheat-sheet

| operation | latency | cost |
|---|---|---|
| investigate / rerun | 20–90 s | ~$0.03 |
| ask (chat) | 5–30 s | ~$0.01 |
| self-review | ~5 s/case + synthesis | ~$0.01/case |
| propose (incl. backtest) | 10–30 s | ~$0.01–0.02 |
| backtest only | < 5 s | free (no model) |
| alerts / failures / missed / rules / reports GET | instant | free |

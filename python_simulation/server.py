"""Live backend for the investigation loop: real agent, real backtests, SSE.

    /opt/miniconda3/envs/waleed/bin/python server.py   # reads .env for the key
    open http://localhost:8000

Every agent interaction is a genuine model call against the live graph tools,
as-of pinned; the agent never sees ground-truth labels. Persistence is
file-backed (store.py) -- the FastAPI backend mirrors these files later.

Endpoints (F1 detection loop / F2 tuning / F3 rule lab -- docs/REQUIREMENTS.md):
  GET  /                          the UI
  GET  /api/alerts                alerts + assignment + case/label state
  GET  /api/investigate?id=       SSE: live investigation -> Case (persisted)
  POST /api/cases/<id>/label      {label: mule|normal|suspicious, note}
  POST /api/cases/<id>/ask        SSE: follow-up chat with the agent
  GET  /api/tuning/failures       severe agent-wrong cases
  POST /api/tuning/build          compile exemplar cards (out/exemplars.json)
  POST /api/tuning/rerun/<id>     SSE: re-investigate with exemplars, diff old/new
  GET|POST /api/reports           victim-report intake (F3 seeds)
  POST /api/rule_lab/propose      SSE: agent proposes rule -> validate -> backtest
  POST /api/rule_lab/backtest     score one rule (no agent)
  GET|POST /api/rules             saved rules (+ the built-in 10)
"""

from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO = Path(__file__).parent
KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()


def load_env() -> None:
    """Copy KEY=value lines from a local .env into os.environ (so we need no
    python-dotenv). Must run before the Anthropic client reads the key."""
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("\"'"))


load_env()

import anthropic
import polars as pl
from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS

import loop
import store
from agent import agent, chat, tools, tuning
from system import rule_lab
from system.detector import detect
from system.rules import RULES, load_thresholds
from system.sampler import to_alert_json

# Haiku v3 is the best-validated config (docs/RESULTS.md): evidence-ledger prompt,
# model decides the action directly. Cheap enough to run live (~$0.03/case).
DEMO_MODEL = "claude-haiku-4-5"
DEMO_VERSION = "v3"

# The 8 demo alerts: 3 real mules + 5 false positives from the realistic regime
# (Sep 1-10). Selection used labels; the agent does not.
DEMO_ALERT_IDS = [
    "AL-02475786", "AL-02533989", "AL-03007359", "AL-03274366",
    "AL-01854515", "AL-04538370", "AL-04598260", "AL-03732458",
]
NFC_DEMO_ACCOUNT = "811A64F10"
NFC_DEMO_BANK = "119"

app = Flask(__name__)
CORS(app)

_CLIENT = anthropic.Anthropic(max_retries=8)   # shared, thread-safe
_ALERTS: list[dict] = []                        # the 8 alerts, time-ordered
_LAUND: set[tuple[str, date]] = set()           # (account, day) laundering truth
_EXISTING_CASE_DAYS: set[tuple[str, date]] = set()   # today's rules' full alert set
_AGENT_SEM = threading.Semaphore(3)             # cap concurrent live agent runs
_NFC_LOCK = threading.Lock()
_NFC_STORE: dict = {}
_NFC_SUBMISSIONS: dict[str, dict] = {}
_THRESHOLDS: dict[str, float] | None = None


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def sse_response(worker) -> Response:
    """Run `worker(emit)` in a thread; stream every emitted event, then stop.
    The worker must emit its own terminal done/error event."""
    def stream():
        events: queue.Queue = queue.Queue()

        def run():
            with _AGENT_SEM:
                try:
                    worker(events.put)
                except Exception as e:                         # noqa: BLE001
                    events.put({"type": "error",
                                "message": f"{type(e).__name__}: {e}"})
            events.put(None)

        threading.Thread(target=run, daemon=True).start()
        for event in iter(events.get, None):
            yield sse(event)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def is_real(alert: dict) -> bool | None:
    """Post-hoc truth for the badge -- never shown to the agent."""
    if alert.get("source") == "nfc":
        return None
    day = date.fromisoformat(alert["txn"]["timestamp"][:10])
    return (alert["subject_account"], day) in _LAUND


def alert_by_id(alert_id: str) -> dict | None:
    with _NFC_LOCK:
        return next((a for a in _ALERTS if a["alert_id"] == alert_id), None)


def new_alert_id() -> str:
    """Return an unused ID with the same display shape as batch alerts."""
    existing = {alert["alert_id"] for alert in _ALERTS}
    while True:
        alert_id = f"AL-{uuid.uuid4().int % 100_000_000:08d}"
        if alert_id not in existing:
            return alert_id


def case_by_id(case_id: str) -> dict | None:
    return store.latest_by(store.CASES, "case_id").get(case_id)


def load() -> None:
    """Load the graph once, pick the demo alerts, build the truth sets."""
    global _THRESHOLDS
    print("loading transaction graph (~30s, ~3GB)...", flush=True)
    tools.init()
    _THRESHOLDS = load_thresholds()

    by_id = {}
    all_case_days = set()
    for line in open(REPO / "out" / "alerts_stream.jsonl"):
        a = json.loads(line)
        all_case_days.add((a["subject_account"],
                           date.fromisoformat(a["txn"]["timestamp"][:10])))
        if a["alert_id"] in DEMO_ALERT_IDS:
            by_id[a["alert_id"]] = a
    _ALERTS[:] = sorted(by_id.values(), key=lambda a: a["txn"]["timestamp"])
    _EXISTING_CASE_DAYS.update(all_case_days)

    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    la = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(10).alias("il")).filter(pl.col("il") == 1).collect())
    _LAUND.update(la.select("sa", "day").iter_rows())
    _LAUND.update(la.select("ra", "day").iter_rows())
    print(
        f"NFC HIGH_AMOUNT threshold: {_THRESHOLDS['amount_paid']}",
        flush=True,
    )
    print(f"ready: {len(_ALERTS)} alerts, http://localhost:8000", flush=True)


# --------------------------------------------------------------- F1: the loop

@app.route("/")
def index():
    return send_file(REPO / "ui" / "index.html")


@app.route("/api/alerts")
def api_alerts():
    """Alerts enriched with assignment, investigation, and label state -- the
    UI restores the whole queue from this one call."""
    with _NFC_LOCK:
        alerts = list(_ALERTS)
    labels = store.read_all(store.LABELS)
    assigned = loop.assign_all(alerts, labels)
    label_by_alert = {l["alert_id"]: l for l in labels}
    case_by_alert = {c["alert"]["alert_id"]: c
                     for c in store.latest_by(store.CASES, "case_id").values()}
    out = []
    for a in sorted(
        alerts,
        key=lambda alert: alert["txn"]["timestamp"],
        reverse=True,
    ):
        aid = a["alert_id"]
        case = case_by_alert.get(aid)
        lab = label_by_alert.get(aid)
        out.append({
            "alert_id": aid,
            "subject_account": a["subject_account"],
            "timestamp": a["txn"]["timestamp"],
            "rules_fired": a["rules_fired"],
            "assigned_to": assigned.get(aid),
            "case": case,
            "label": (None if lab is None else
                      {k: lab[k] for k in ("label", "final_decision",
                                           "disagreement", "severe",
                                           "labeled_by", "ts")}),
        })
    return jsonify(out)


def _required_text(container: dict, key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _nfc_transaction(body: dict) -> tuple[str, dict]:
    transaction_id = str(uuid.UUID(_required_text(body, "transaction_id")))
    card = body.get("real_card_data")
    anomaly = body.get("anomaly_context")
    if not isinstance(card, dict) or not isinstance(anomaly, dict):
        raise ValueError("real_card_data and anomaly_context are required")

    pan = _required_text(card, "pan")
    if not pan.isdigit() or not 12 <= len(pan) <= 19:
        raise ValueError("pan must contain 12 to 19 digits")
    timestamp_text = _required_text(body, "timestamp")
    try:
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    timestamp = timestamp.astimezone(timezone.utc)

    try:
        amount = Decimal(str(anomaly.get("amount")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("amount must be numeric") from error
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be positive")

    currency = _required_text(anomaly, "currency").upper()
    if len(currency) != 3:
        raise ValueError("currency must be a three-letter code")
    merchant = _required_text(anomaly, "merchant_name")
    country = _required_text(anomaly, "country").upper()
    internal_id = uuid.UUID(transaction_id).int & 0x7FFFFFFF

    transaction = {
        "txn_id": internal_id,
        "ts": timestamp.replace(tzinfo=None),
        "ts_us": int(timestamp.timestamp() * 1_000_000),
        "sender_bank": NFC_DEMO_BANK,
        "sender_account": NFC_DEMO_ACCOUNT,
        "receiver_bank": f"POS-{country}",
        "receiver_account": merchant,
        "amount_paid": float(amount),
        "payment_currency": currency,
        "amount_received": float(amount),
        "receiving_currency": currency,
        "payment_format": "Credit Card",
    }
    return transaction_id, transaction


@app.route("/api/nfc/transactions", methods=["POST"])
def api_nfc_transactions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"detail": "JSON object required"}), 400

    try:
        transaction_id, transaction = _nfc_transaction(body)
    except ValueError as error:
        return jsonify({"detail": str(error)}), 422

    fingerprint = json.dumps(body, sort_keys=True, separators=(",", ":"))
    with _NFC_LOCK:
        existing = _NFC_SUBMISSIONS.get(transaction_id)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                return jsonify({
                    "detail": "Transaction ID already exists with a different payload"
                }), 409
            return jsonify({
                "transaction_id": transaction_id,
                "status": existing["status"],
                "duplicate": True,
            }), 200

        if _THRESHOLDS is None:
            return jsonify({"detail": "Fraud detector is not ready"}), 503

        candidate = detect(_NFC_STORE, transaction, _THRESHOLDS)
        status = "approved"
        if candidate is not None:
            alert = to_alert_json(candidate, datetime.now(timezone.utc))
            alert["alert_id"] = new_alert_id()
            alert["source"] = "nfc"
            alert["txn"]["txn_id"] = transaction_id
            _ALERTS.append(alert)
            _ALERTS.sort(key=lambda item: item["txn"]["timestamp"])
            status = "flagged"

        _NFC_SUBMISSIONS[transaction_id] = {
            "fingerprint": fingerprint,
            "payload": body,
            "status": status,
        }

    return jsonify({
        "transaction_id": transaction_id,
        "status": status,
        "duplicate": False,
    }), 201


@app.route("/api/investigate")
def api_investigate():
    """SSE: one live investigation; the finished Case persists to cases_live."""
    alert = alert_by_id(request.args.get("id", ""))
    if alert is None:
        return jsonify({"error": "unknown alert"}), 404
    exemplars = store.read_json(store.EXEMPLARS, None)

    def worker(emit):
        case = agent.investigate(alert, model=DEMO_MODEL, version=DEMO_VERSION,
                                 client=_CLIENT, on_step=emit,
                                 exemplars=exemplars)
        store.append(store.CASES, case)
        emit({"type": "done", "case": case, "truth": {"real": is_real(alert)}})

    return sse_response(worker)


@app.route("/api/cases/<case_id>/label", methods=["POST"])
def api_label(case_id: str):
    """File the investigator's verdict (D1); severe failures harvest here (D2)."""
    body = request.get_json(force=True)
    label = body.get("label")
    if label not in loop.AGREES_WITH:
        return jsonify({"error": "label must be mule|normal|suspicious"}), 400
    case = case_by_id(case_id)
    if case is None:
        return jsonify({"error": "unknown case"}), 404
    alert_id = case["alert"]["alert_id"]
    assigned = loop.assign_all(_ALERTS, store.read_all(store.LABELS))
    verdictd = loop.judge(label, case["recommendation"])
    record = {
        "case_id": case_id, "alert_id": alert_id, "label": label,
        "note": body.get("note", ""),
        "agent_recommendation": case["recommendation"],
        "agent_confidence": case["confidence"],
        "prompt_version": case["_meta"]["prompt_version"],
        "labeled_by": assigned.get(alert_id),
        "ts": datetime.now(timezone.utc).isoformat(),
        **verdictd,
    }
    store.append(store.LABELS, record)
    return jsonify(record)


@app.route("/api/cases/<case_id>/ask", methods=["POST"])
def api_ask(case_id: str):
    """SSE: follow-up question to the agent about a filed case."""
    body = request.get_json(force=True)
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    case = case_by_id(case_id)
    if case is None:
        return jsonify({"error": "unknown case"}), 404
    alert = alert_by_id(case["alert"]["alert_id"]) or case["alert"]
    history = body.get("history") or []

    def worker(emit):
        answer = chat.ask(alert, case, question, history,
                          model=DEMO_MODEL, client=_CLIENT, on_step=emit)
        emit({"type": "done", "answer": answer})

    return sse_response(worker)


# ------------------------------------------------------------------ F2: tuning

@app.route("/api/tuning/failures")
def api_failures():
    return jsonify(tuning.failures())


@app.route("/api/tuning/review", methods=["POST"])
def api_review():
    """SSE: the agent reviews its OWN disagreed cases -- streamed per-case
    post-mortems, then the distilled adaptation proposal. Installs nothing;
    the human approves via /api/tuning/approve."""
    fails = tuning.failures()
    if not fails:
        return jsonify({"error": "no disagreements to review"}), 400
    cases = store.latest_by(store.CASES, "case_id")

    def worker(emit):
        emit({"type": "start", "n": len(fails)})
        doc = tuning.self_review(fails, cases, _CLIENT, DEMO_MODEL, emit)
        emit({"type": "done", "review": doc})

    return sse_response(worker)


@app.route("/api/tuning/approve", methods=["POST"])
def api_approve():
    """Install the human-approved adaptation as the new exemplar set. Cards
    gain their alert_id here (needed for leave-one-out in the agent)."""
    body = request.get_json(force=True)
    cards = body.get("cards") or []
    if not cards:
        return jsonify({"error": "no cards to install"}), 400
    cases = store.latest_by(store.CASES, "case_id")
    for c in cards:
        case = cases.get(c.get("case_id", ""))
        c["alert_id"] = case["alert"]["alert_id"] if case else None
    n_labels = len(store.read_all(store.LABELS))
    prev = store.read_json(store.EXEMPLARS, {"version": 0})
    doc = {
        "version": prev.get("version", 0) + 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "approved": True,
        "n_labels": n_labels,
        "error_patterns": body.get("error_patterns", []),
        "summary": body.get("summary", ""),
        "tau": {"changed": False,
                "note": (f"threshold re-sweep needs >= "
                         f"{tuning.MIN_LABELS_FOR_TAU} labels (have "
                         f"{n_labels}); prompt corrections only")},
        "cards": cards,
    }
    store.write_json(store.EXEMPLARS, doc)
    return jsonify({"version": doc["version"], "n_cards": len(cards)})


@app.route("/api/tuning/rerun/<case_id>", methods=["POST"])
def api_rerun(case_id: str):
    """SSE: genuinely re-investigate a failed case with the current exemplars;
    stream the tools; end with the old-vs-new report diff."""
    old = case_by_id(case_id)
    if old is None:
        return jsonify({"error": "unknown case"}), 404
    lab = next((l for l in reversed(store.read_all(store.LABELS))
                if l["case_id"] == case_id), None)
    if lab is None:
        return jsonify({"error": "case has no label; label it first"}), 400
    exemplars = store.read_json(store.EXEMPLARS, None)
    if not exemplars or not exemplars.get("cards"):
        return jsonify({"error": "no exemplars built; POST /api/tuning/build first"}), 400
    alert = alert_by_id(old["alert"]["alert_id"]) or old["alert"]
    attempt = 1 + sum(1 for r in store.read_all(store.RERUNS)
                      if r["case_id"] == case_id)

    def worker(emit):
        new = agent.investigate(alert, model=DEMO_MODEL, version=DEMO_VERSION,
                                client=_CLIENT, on_step=emit, exemplars=exemplars)
        out = tuning.outcome(old["recommendation"], new["recommendation"],
                             lab["label"])
        diff = {
            "recommendation": [old["recommendation"], new["recommendation"]],
            "confidence": [old["confidence"], new["confidence"]],
            "typology": [old["typology"], new["typology"]],
            "human_label": lab["label"],
        }
        record = {"case_id": case_id, "attempt": attempt, "outcome": out,
                  "prompt_version": new["_meta"]["prompt_version"],
                  "diff": diff, "ts": datetime.now(timezone.utc).isoformat()}
        store.append(store.RERUNS, record)
        emit({"type": "done", "old_case": old, "new_case": new,
              "diff": diff, "outcome": out, "attempt": attempt,
              "data_ceiling": out == "still_wrong" and attempt >= 2})

    return sse_response(worker)


# --------------------------------------------------------------- F3: rule lab

@app.route("/api/missed")
def api_missed():
    """Known missed cases (offline evaluation, eval/missed.py). The typology
    is stripped here: these are UNDISCOVERED patterns -- neither the UI nor
    the agent gets to know their ground-truth shape (D9)."""
    missed = store.read_all(REPO / "out" / "missed_attempts.jsonl")
    reports = store.read_all(store.REPORTS)
    reported = {r["subject_account"] for r in reports}
    for m in missed:
        m.pop("typology", None)
        m["reported"] = m["suggested_subject"] in reported
    return jsonify(missed)


@app.route("/api/reports", methods=["GET", "POST"])
def api_reports():
    if request.method == "POST":
        body = request.get_json(force=True)
        missing = [k for k in ("subject_account", "window_start", "window_end")
                   if not body.get(k)]
        if missing:
            return jsonify({"error": f"missing: {missing}"}), 400
        report = {
            "report_id": f"RPT-{len(store.read_all(store.REPORTS)) + 1:04d}",
            "subject_account": body["subject_account"].strip(),
            "window_start": body["window_start"], "window_end": body["window_end"],
            "description": body.get("description", ""),
            "reported_by": body.get("reported_by", "investigator"),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        report["n_txns"] = rule_lab._subject_window(report).height
        store.append(store.REPORTS, report)
        return jsonify(report), 201

    saved = store.read_json(store.RULES_ACCEPTED, [])
    rule_created = {rid for r in saved for rid in r.get("source_report_ids", [])}
    return jsonify([{**r, "status": ("rule_created" if r["report_id"] in rule_created
                                     else "new")}
                    for r in store.read_all(store.REPORTS)])


def _existing_rules_summary() -> list[dict]:
    builtin = [{"name": n, "weight": w, "subject_side": s} for n, w, s, _ in RULES]
    saved = [{"name": r["rule"]["name"], "weight": r["rule"]["weight"],
              "subject_side": r["rule"]["subject_side"],
              "conjuncts": r["rule"]["conjuncts"]}
             for r in store.read_json(store.RULES_ACCEPTED, [])]
    return builtin + saved


@app.route("/api/rule_lab/propose", methods=["POST"])
def api_propose():
    """SSE: contrast pack -> agent proposes DSL -> validator -> real backtest."""
    body = request.get_json(force=True)
    report = next((r for r in store.read_all(store.REPORTS)
                   if r["report_id"] == body.get("report_id")), None)
    if report is None:
        return jsonify({"error": "unknown report_id"}), 404

    def worker(emit):
        emit({"type": "resolving",
              "label": f"Resolving {report['subject_account']}'s transactions "
                       f"and feature profile for the reported window"})
        pack = rule_lab.contrast_pack(report)
        if pack["n_txns"] == 0:
            emit({"type": "error",
                  "message": "no transactions found for that account in that window"})
            return
        emit({"type": "resolved", "n_txns": pack["n_txns"]})
        emit({"type": "proposing",
              "label": "Agent is contrasting the case against population "
                       "quantiles and drafting a rule"})
        def check(rule):
            problems = rule_lab.validate(rule)
            if not problems and not rule_lab.satisfiable(rule, report):
                problems = ["no transaction of the reported case satisfies all "
                            "conjuncts jointly -- rule cannot catch its own seed"]
            return problems

        rule = rule_lab.propose(report, _existing_rules_summary(), _CLIENT,
                                DEMO_MODEL)
        emit({"type": "rule_draft", "rule": rule})
        problems = check(rule)
        if problems:                              # one validator-feedback retry
            emit({"type": "revising",
                  "label": f"Validator rejected the draft ({problems[0][:80]}...); "
                           f"agent is revising", "problems": problems})
            rule = rule_lab.propose(report, _existing_rules_summary(), _CLIENT,
                                    DEMO_MODEL, feedback="\n".join(problems))
            emit({"type": "rule_draft", "rule": rule})
            problems = check(rule)
        if problems:
            emit({"type": "error", "message": f"validator rejected: {problems}"})
            return
        emit({"type": "backtesting",
              "label": "Simulating the rule over all 5M transactions (17 days)"})
        report_card = rule_lab.backtest(rule, _LAUND, _EXISTING_CASE_DAYS, report)
        emit({"type": "done", "rule": rule, "backtest": report_card,
              "report_id": report["report_id"]})

    return sse_response(worker)


@app.route("/api/rule_lab/backtest", methods=["POST"])
def api_backtest():
    body = request.get_json(force=True)
    rule = body.get("rule") or {}
    problems = rule_lab.validate(rule)
    if problems:
        return jsonify({"error": f"invalid rule: {problems}"}), 400
    report = next((r for r in store.read_all(store.REPORTS)
                   if r["report_id"] == body.get("report_id")), None)
    return jsonify(rule_lab.backtest(rule, _LAUND, _EXISTING_CASE_DAYS, report))


@app.route("/api/rules", methods=["GET", "POST"])
def api_rules():
    if request.method == "POST":
        body = request.get_json(force=True)
        rule, backtest = body.get("rule") or {}, body.get("backtest") or {}
        problems = rule_lab.validate(rule)
        if problems:
            return jsonify({"error": f"invalid rule: {problems}"}), 400
        saved = store.read_json(store.RULES_ACCEPTED, [])
        entry = {
            "rule": rule, "backtest": backtest,
            "provenance": "agent_proposed", "enabled": False,
            "source_report_ids": ([body["report_id"]]
                                  if body.get("report_id") else []),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        saved.append(entry)
        store.write_json(store.RULES_ACCEPTED, saved)
        return jsonify(entry), 201

    builtin = [{"rule": {"name": n, "weight": w, "subject_side": s},
                "provenance": "hand_written", "enabled": True}
               for n, w, s, _ in RULES]
    return jsonify(builtin + store.read_json(store.RULES_ACCEPTED, []))


if __name__ == "__main__":
    load()
    app.run(host="0.0.0.0", port=8000, threaded=True)  # threaded: concurrent streams

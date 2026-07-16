"""Live demo backend: runs the REAL agent and streams it to the browser.

    /opt/miniconda3/envs/waleed/bin/python server.py   # reads .env for the key
    open http://localhost:8000

Not a replay. Each investigation is a genuine `agent.investigate()` call against
the Haiku model, using the same graph tools the batch runs use, pinned to the
alert's timestamp. The agent never sees labels; the server knows them only to
score the verdict AFTER the fact (the caught/cleared/missed badge), and that
truth is sent to the browser only in the final `done` event -- never to the model.

Endpoints:
  GET /                     the UI
  GET /api/alerts           the 8 demo alerts, in time order
  GET /api/investigate?id=  Server-Sent Events: one event per agent step, then
                            one {type:"done", case, truth} (or {type:"error"})
"""

from __future__ import annotations

import json
import os
import queue
import threading
from datetime import date
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

from agent import agent, tools

# Haiku v3 is the best-validated config (docs/RESULTS.md): evidence-ledger prompt,
# model decides the action directly. Cheap enough to run live (~$0.03/case).
DEMO_MODEL = "claude-haiku-4-5"
DEMO_VERSION = "v3"

# The 8 demo alerts: 3 real mules + 5 false positives from the realistic regime
# (Sep 1-10) -- enough for a catch and some noise-clearing. Selection used labels;
# the agent does not.
DEMO_ALERT_IDS = [
    "AL-02475786", "AL-02533989", "AL-03007359", "AL-03274366",
    "AL-01854515", "AL-04538370", "AL-04598260", "AL-03732458",
]

app = Flask(__name__)
CORS(app)

_CLIENT = anthropic.Anthropic(max_retries=8)   # shared, thread-safe
_ALERTS: list[dict] = []                        # the 8 alerts, time-ordered
_LAUND: set[tuple[str, date]] = set()           # (account, day) laundering truth


def sse(event: dict) -> str:
    """Format one dict as a Server-Sent Events frame."""
    return f"data: {json.dumps(event, default=str)}\n\n"


def is_real(alert: dict) -> bool:
    """Post-hoc truth for the badge -- never shown to the agent."""
    day = date.fromisoformat(alert["txn"]["timestamp"][:10])
    return (alert["subject_account"], day) in _LAUND


def load() -> None:
    """Load the graph once, pick the 8 demo alerts, build the laundering set."""
    print("loading transaction graph (~30s, ~3GB)...", flush=True)
    tools.init()

    by_id = {}
    for line in open(REPO / "out" / "alerts_stream.jsonl"):
        a = json.loads(line)
        if a["alert_id"] in DEMO_ALERT_IDS:
            by_id[a["alert_id"]] = a
    _ALERTS[:] = sorted(by_id.values(), key=lambda a: a["txn"]["timestamp"])

    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    la = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(10).alias("il")).filter(pl.col("il") == 1).collect())
    _LAUND.update(la.select("sa", "day").iter_rows())
    _LAUND.update(la.select("ra", "day").iter_rows())
    print(f"ready: {len(_ALERTS)} alerts, http://localhost:8000", flush=True)


@app.route("/")
def index():
    return send_file(REPO / "ui" / "index.html")


@app.route("/api/alerts")
def api_alerts():
    return jsonify([{"alert_id": a["alert_id"],
                     "subject_account": a["subject_account"],
                     "timestamp": a["txn"]["timestamp"],
                     "rules_fired": a["rules_fired"]} for a in _ALERTS])


@app.route("/api/investigate")
def api_investigate():
    """Stream one real investigation as it happens. The agent runs in a worker
    thread and reports each step via on_step; we relay those events, then a
    terminal done/error, then a None that stops the stream."""
    alert = next((a for a in _ALERTS if a["alert_id"] == request.args.get("id")), None)
    if alert is None:
        return jsonify({"error": "unknown alert"}), 404

    def stream():
        events: queue.Queue = queue.Queue()

        def run():
            try:
                case = agent.investigate(alert, model=DEMO_MODEL,
                                         version=DEMO_VERSION, client=_CLIENT,
                                         on_step=events.put)
                events.put({"type": "done", "case": case,
                            "truth": {"real": is_real(alert)}})
            except Exception as e:                             # noqa: BLE001
                events.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
            events.put(None)                                   # stops the stream

        threading.Thread(target=run, daemon=True).start()
        for event in iter(events.get, None):
            yield sse(event)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    load()
    app.run(host="127.0.0.1", port=8000, threaded=True)  # threaded: concurrent streams

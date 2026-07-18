"""File-backed stores for the investigation loop (labels, cases, reports,
rules, exemplars). JSONL append + read; no database. These files ARE the
system of record for the demo -- the FastAPI backend mirrors them later
(REQUIREMENTS.md D3). All under out/, gitignored.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

OUT = Path(__file__).parent / "out"

CASES = OUT / "cases_live.jsonl"          # every finished investigation
LABELS = OUT / "labels.jsonl"             # investigator label events (append-only)
REPORTS = OUT / "reported_cases.jsonl"    # victim-report intake (F3 seeds)
RERUNS = OUT / "reruns.jsonl"             # tuning rerun diffs (F2)
RULES_ACCEPTED = OUT / "rules_accepted.json"   # saved Rule Lab rules (a JSON list)
EXEMPLARS = OUT / "exemplars.json"        # tuning exemplar cards (agent prompt input)

_LOCK = threading.Lock()                  # server is threaded; appends must not interleave


def append(path: Path, obj: dict) -> None:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(obj, default=str) + "\n")


def read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in open(path) if line.strip()]


def latest_by(path: Path, key: str) -> dict[str, dict]:
    """Fold an append-only jsonl into {key: latest record}."""
    out: dict[str, dict] = {}
    for rec in read_all(path):
        out[rec[key]] = rec
    return out


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, obj) -> None:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, default=str))

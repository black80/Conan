"""The sync detection path: one transaction in, one candidate alert (or None)
out, in microseconds.

    store = feature_store.new_store()
    thr = rules.load_thresholds()
    candidate = detect(store, txn, thr)

This is deliberately just glue -- the feature store lives in feature_store.py,
the rules live in rules.py. Read those to understand the system.
"""

from __future__ import annotations

from . import feature_store, rules


def detect(store: dict, txn: dict, thr: dict, min_score: int = 1) -> dict | None:
    f = feature_store.update(store, txn)
    verdict = rules.score(txn, f, thr)
    if verdict["score"] < min_score:
        return None
    return {**txn, **f, **verdict}

"""THE FEATURE STORE.

One entry per account, held in RAM, updated in place as transactions stream
through. This is what lets the rules answer in microseconds: the account's
history is already summarized here, so no transaction scan ever happens on
the hot path.

    store = new_store()
    feats = update(store, txn)     # advance both accounts, get the feature row

Feature families (per account, both directions, windows 24h and 7d):

  volumes      n_out_1d/7d, amt_out_1d/7d, n_in_1d/7d, sender_inflow_1d/7d,
               max_out_1d, net_flow_1d/7d (in - out: accumulating vs passing)
  first-seen   is_first_seen_pair (first-ever payment between this pair),
               fs_in_cnt/sum_1d/7d (money arriving from NEW senders -- the
               recruited-mule signature), burst_in/out (share of lifetime
               counterparties acquired in the last 24h)
  pair         pair_n_txns ("5th payment to this receiver"),
               pair_age_days (relationship age)
  recency      days_since_last_out / days_since_last_in (dormancy wake-up)
  ratios       collect_ratio, spray_ratio (hub-invariant), passthrough_ratio
  baseline     sender_avg (lifetime mean outbound BEFORE this payment)

What one entry looks like:

    store["8000F4580"] = {
        "cps_in":  {cp: [n_txns, first_te]},   # every account that ever paid me
        "cps_out": {cp: [n_txns, first_te]},   # every account I ever paid
        "in_1d":  deque[(te, amt)], "in_7d":  deque[(te, amt)],  # + running sums
        "out_1d": deque[(te, amt)], "out_7d": deque[(te, amt)],
        "fs_in_1d"/"fs_in_7d": deque[(te, amt)],  # inbound from first-seen senders
        "fs_out_1d": deque[te],                   # first-seen payees (burst_out)
        "out_max": deque[(te, amt)],              # monotonic 24h max
        "life_out_sum": .., "life_out_n": ..,
        "last_in_te": .., "last_out_te": ..,
    }

Timing semantics (kept EXACTLY equal to the batch twin, engine.py -- proven by
eval/parity.py): events are ordered by (ts, txn_id); a txn's receiver-inbound
event lands 1us before its sender-outbound event; rolling windows are
closed-right; expanding counts include the current event; sender_avg and the
days_since_last_* gaps are measured BEFORE the current event. No value ever
looks forward.

The label is not in this module's vocabulary at all.
"""

from __future__ import annotations

from collections import deque

DAY_US = 86_400_000_000
WEEK_US = 7 * DAY_US


def new_store() -> dict:
    return {}


def _blank() -> dict:
    return {
        "cps_in": {}, "cps_out": {},               # cp -> [n_txns, first_te]
        "in_1d": deque(), "out_1d": deque(),       # (te, amount), 24h
        "in_7d": deque(), "out_7d": deque(),       # (te, amount), 7d
        "in_sum_1d": 0.0, "out_sum_1d": 0.0,
        "in_sum_7d": 0.0, "out_sum_7d": 0.0,
        "fs_in_1d": deque(), "fs_in_7d": deque(),  # (te, amount) from NEW senders
        "fs_in_sum_1d": 0.0, "fs_in_sum_7d": 0.0,
        "fs_out_1d": deque(),                      # te of first-seen payees
        "out_max": deque(),                        # monotonic-max (te, amount)
        "life_out_sum": 0.0, "life_out_n": 0,
        "last_in_te": None, "last_out_te": None,
    }


def _evict(s: dict, now: int) -> None:
    """Drop everything that fell out of its window (closed-right)."""
    c1, c7 = now - DAY_US, now - WEEK_US
    for dq, key in ((s["in_1d"], "in_sum_1d"), (s["out_1d"], "out_sum_1d"),
                    (s["fs_in_1d"], "fs_in_sum_1d")):
        while dq and dq[0][0] <= c1:
            s[key] -= dq.popleft()[1]
    for dq, key in ((s["in_7d"], "in_sum_7d"), (s["out_7d"], "out_sum_7d"),
                    (s["fs_in_7d"], "fs_in_sum_7d")):
        while dq and dq[0][0] <= c7:
            s[key] -= dq.popleft()[1]
    fo = s["fs_out_1d"]
    while fo and fo[0] <= c1:
        fo.popleft()
    om = s["out_max"]
    while om and om[0][0] <= c1:
        om.popleft()


def update(store: dict, txn: dict) -> dict:
    """Advance both accounts' state through this transaction and return the
    transaction's feature row -- the as-of-now summary the rules score and the
    agent's case file opens with.

    txn needs: txn_id, ts_us (int microseconds since epoch), sender_account,
    receiver_account, amount_paid, amount_received.
    """
    te_recv = txn["ts_us"] + 2 * txn["txn_id"]
    te_send = te_recv + 1
    sa, ra = txn["sender_account"], txn["receiver_account"]

    # ---- receiver-inbound event
    r = store.get(ra)
    if r is None:
        r = store[ra] = _blank()
    pair_in = r["cps_in"].get(sa)
    first_seen_pair = pair_in is None
    if first_seen_pair:
        r["cps_in"][sa] = pair_in = [0, te_recv]
    pair_in[0] += 1
    r_days_since_last_in = (
        (te_recv - r["last_in_te"]) / DAY_US if r["last_in_te"] is not None else None)
    _evict(r, te_recv)
    amt_in = txn["amount_received"]
    r["in_1d"].append((te_recv, amt_in))
    r["in_7d"].append((te_recv, amt_in))
    r["in_sum_1d"] += amt_in
    r["in_sum_7d"] += amt_in
    if first_seen_pair:
        r["fs_in_1d"].append((te_recv, amt_in))
        r["fs_in_7d"].append((te_recv, amt_in))
        r["fs_in_sum_1d"] += amt_in
        r["fs_in_sum_7d"] += amt_in
    r["last_in_te"] = te_recv
    r_fan_in = len(r["cps_in"])
    # Snapshot receiver features AT THE RECEIVER EVENT: for self-transfers the
    # sender block below mutates this same dict 1us later, and the receiver
    # must not see its own outbound leg (batch twin semantics).
    recv_feats = {
        "r_fan_in": r_fan_in, "r_fan_out": len(r["cps_out"]),
        "r_n_in_1d": len(r["in_1d"]), "r_n_in_7d": len(r["in_7d"]),
        "r_amt_in_1d": r["in_sum_1d"], "r_amt_in_7d": r["in_sum_7d"],
        "r_n_out_1d": len(r["out_1d"]), "r_amt_out_1d": r["out_sum_1d"],
        "r_fs_in_cnt_1d": len(r["fs_in_1d"]), "r_fs_in_sum_1d": r["fs_in_sum_1d"],
        "r_fs_in_cnt_7d": len(r["fs_in_7d"]), "r_fs_in_sum_7d": r["fs_in_sum_7d"],
        "r_days_since_last_in": r_days_since_last_in,
        "r_burst_in": len(r["fs_in_1d"]) / max(r_fan_in, 1),
        "collect_ratio": r_fan_in / (len(r["cps_out"]) + 1),
    }

    # ---- sender-outbound event
    s = store.get(sa)
    if s is None:
        s = store[sa] = _blank()
    sender_avg = (s["life_out_sum"] / s["life_out_n"]) if s["life_out_n"] else None
    days_since_last_out = (
        (te_send - s["last_out_te"]) / DAY_US if s["last_out_te"] is not None else None)
    days_since_last_in = (
        (te_send - s["last_in_te"]) / DAY_US if s["last_in_te"] is not None else None)
    pair_out = s["cps_out"].get(ra)
    if pair_out is None:
        s["cps_out"][ra] = pair_out = [0, te_send]
        s["fs_out_1d"].append(te_send)
    pair_out[0] += 1
    _evict(s, te_send)
    amt = txn["amount_paid"]
    s["out_1d"].append((te_send, amt))
    s["out_7d"].append((te_send, amt))
    s["out_sum_1d"] += amt
    s["out_sum_7d"] += amt
    om = s["out_max"]
    while om and om[-1][1] <= amt:
        om.pop()
    om.append((te_send, amt))
    s["life_out_sum"] += amt
    s["life_out_n"] += 1
    s["last_out_te"] = te_send

    fan_in = len(s["cps_in"])
    fan_out = len(s["cps_out"])
    amt_out_1d = s["out_sum_1d"]
    sender_inflow_1d = s["in_sum_1d"]
    hi = max(sender_inflow_1d, amt_out_1d)

    return {
        # sender volumes
        "fan_in": fan_in, "fan_out": fan_out,
        "n_out_1d": len(s["out_1d"]), "n_out_7d": len(s["out_7d"]),
        "amt_out_1d": amt_out_1d, "amt_out_7d": s["out_sum_7d"],
        "n_in_1d": len(s["in_1d"]), "n_in_7d": len(s["in_7d"]),
        "sender_inflow_1d": sender_inflow_1d, "sender_inflow_7d": s["in_sum_7d"],
        "max_out_1d": om[0][1],
        "net_flow_1d": sender_inflow_1d - amt_out_1d,
        "net_flow_7d": s["in_sum_7d"] - s["out_sum_7d"],
        # sender first-seen inflow (money from NEW senders)
        "fs_in_cnt_1d": len(s["fs_in_1d"]), "fs_in_sum_1d": s["fs_in_sum_1d"],
        "fs_in_cnt_7d": len(s["fs_in_7d"]), "fs_in_sum_7d": s["fs_in_sum_7d"],
        # sender recency / baseline / ratios
        "days_since_last_out": days_since_last_out,
        "days_since_last_in": days_since_last_in,
        "sender_avg": sender_avg,
        "burst_out": len(s["fs_out_1d"]) / max(fan_out, 1),
        "spray_ratio": fan_out / (fan_in + 1),
        "passthrough_ratio": (min(sender_inflow_1d, amt_out_1d) / hi) if hi > 0 else 0.0,
        # receiver (snapshotted at the receiver event, see recv_feats above)
        **recv_feats,
        # the pair (this sender -> this receiver relationship)
        "is_first_seen_pair": first_seen_pair,
        "pair_n_txns": pair_out[0],
        "pair_age_days": (te_send - pair_out[1]) / DAY_US,
    }

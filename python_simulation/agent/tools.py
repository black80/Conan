"""Read-only query tools for the investigation agent.

Person B's agent loop calls these; every function returns JSON-serializable
dicts/lists so results can go straight into a tool-call response and into
Case.reasoning_trace.

Anchor discipline: every time-scoped function takes `as_of` (ISO string or
datetime). Pass the alert transaction's timestamp so the agent only sees what
existed when the alert fired; None means "no cap" (full file, fine for a
post-hoc review demo, but say so in the trace).

The label (`Is Laundering`) is NEVER loaded into this module -- the agent
cannot leak what the tool layer does not have.

Usage (from the repo root):
    from agent import tools
    tools.init()                       # default kagglehub paths
    tools.get_account_profile("810BB59D0")
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

_KAGGLE = Path(
    "~/.cache/kagglehub/datasets/ealtman2019/"
    "ibm-transactions-for-anti-money-laundering-aml/versions/8"
).expanduser()
_REPO = Path(__file__).parent.parent

_TRANS: pl.DataFrame | None = None
_ACCOUNTS: pl.DataFrame | None = None
_ALERTS: pl.DataFrame | None = None
_FEAT: pl.DataFrame | None = None       # compact feature-store frame for lookups

_TXN_COLS = ["txn_id", "ts", "sender_bank", "sender_account", "receiver_bank",
             "receiver_account", "amount_paid", "payment_currency",
             "amount_received", "receiving_currency", "payment_format"]

# the discriminating "shape" fields the agent reads for any account (the
# feature store holds many more; these are the ones that separate a mule from
# a busy legit account). sender-side + receiver-side, kept compact on purpose.
_FEAT_COLS = ["sender_account", "receiver_account", "ts", "collect_ratio",
              "spray_ratio", "passthrough_ratio", "r_fan_in", "r_fan_out",
              "fan_in", "fan_out", "r_burst_in", "burst_out", "n_out_1d",
              "r_n_in_1d", "days_since_last_out", "days_since_last_in"]


def init(trans_path: str | None = None, accounts_path: str | None = None,
         alerts_path: str | None = None, features: bool = True) -> None:
    """Load data once (~3 GB RAM for the 5M-row file). Idempotent.
    features=True also builds the compact feature-store frame that backs
    get_account_features (a persistent as-of feature lookup)."""
    global _TRANS, _ACCOUNTS, _ALERTS, _FEAT
    if _TRANS is not None:
        return
    from system.engine import load
    lf = load(trans_path or str(_KAGGLE / "HI-Small_Trans.csv"))
    # label column dropped here, permanently out of the agent's reach
    _TRANS = lf.select(_TXN_COLS).collect(engine="streaming")
    acc = accounts_path or str(_KAGGLE / "HI-Small_accounts.csv")
    if Path(acc).exists():
        _ACCOUNTS = pl.read_csv(acc).rename({
            "Bank Name": "bank_name", "Bank ID": "bank_id",
            "Account Number": "account", "Entity ID": "entity_id",
            "Entity Name": "entity_name",
        })
    al = alerts_path or str(_REPO / "out" / "alerts.parquet")
    if Path(al).exists():
        _ALERTS = pl.read_parquet(al).drop("is_laundering", strict=False)
    if features and trans_path is None:      # only for the real full dataset
        cache = _REPO / "out" / "features_compact.parquet"
        if cache.exists():
            _FEAT = pl.read_parquet(cache)
        else:
            from system.engine import features as build_features
            df = load(str(_KAGGLE / "HI-Small_Trans.csv")).collect(engine="streaming")
            _FEAT = build_features(df).select(_FEAT_COLS)
            del df
            cache.parent.mkdir(parents=True, exist_ok=True)
            _FEAT.write_parquet(cache)


def _trans() -> pl.DataFrame:
    if _TRANS is None:
        init()
    return _TRANS


def _cutoff(df: pl.DataFrame, as_of: str | datetime | None,
            days: float | None = None) -> pl.DataFrame:
    if as_of is not None:
        if isinstance(as_of, str):
            as_of = datetime.fromisoformat(as_of)
        df = df.filter(pl.col("ts") <= as_of)
        if days is not None:
            df = df.filter(pl.col("ts") > as_of - timedelta(days=days))
    elif days is not None:
        end = df["ts"].max()
        if end is not None:
            df = df.filter(pl.col("ts") > end - timedelta(days=days))
    return df


def _involving(account: str) -> pl.DataFrame:
    return _trans().filter(
        (pl.col("sender_account") == account)
        | (pl.col("receiver_account") == account)
    )


def _rows(df: pl.DataFrame) -> list[dict]:
    return [
        {**r, "ts": r["ts"].isoformat()} if isinstance(r.get("ts"), datetime) else r
        for r in df.iter_rows(named=True)
    ]


# ------------------------------------------------------------------- the tools

def get_account_profile(account: str) -> dict:
    """Entity info from Accounts.csv + lifetime activity aggregates."""
    txns = _involving(account)
    out = txns.filter(pl.col("sender_account") == account)
    inc = txns.filter(pl.col("receiver_account") == account)
    profile: dict = {
        "account": account,
        "first_seen": str(txns["ts"].min()) if txns.height else None,
        "last_seen": str(txns["ts"].max()) if txns.height else None,
        "n_outbound": out.height,
        "n_inbound": inc.height,
        "distinct_receivers": out["receiver_account"].n_unique() if out.height else 0,
        "distinct_senders": inc["sender_account"].n_unique() if inc.height else 0,
        "total_out_by_currency": dict(
            out.group_by("payment_currency").agg(pl.col("amount_paid").sum())
            .iter_rows()) if out.height else {},
        "total_in_by_currency": dict(
            inc.group_by("receiving_currency").agg(pl.col("amount_received").sum())
            .iter_rows()) if inc.height else {},
        "payment_formats": sorted(txns["payment_format"].unique()) if txns.height else [],
    }
    if _ACCOUNTS is not None:
        hit = _ACCOUNTS.filter(pl.col("account") == account)
        if hit.height:
            r = hit.row(0, named=True)
            profile |= {"bank_name": r["bank_name"], "entity_id": r["entity_id"],
                        "entity_name": r["entity_name"]}
            siblings = _ACCOUNTS.filter(
                (pl.col("entity_id") == r["entity_id"])
                & (pl.col("account") != account)
            )["account"].to_list()
            profile["other_accounts_same_entity"] = siblings
    return profile


def get_account_history(account: str, days: float = 7,
                        as_of: str | datetime | None = None,
                        limit: int = 200) -> list[dict]:
    """Transactions touching the account in the window, newest first."""
    df = _cutoff(_involving(account), as_of, days).sort("ts", descending=True)
    return _rows(df.head(limit))


def get_counterparties(account: str, direction: str = "in", days: float = 7,
                       as_of: str | datetime | None = None,
                       top: int = 50) -> list[dict]:
    """Who the account transacts with: (counterparty, n_txns, total_amount)."""
    df = _cutoff(_involving(account), as_of, days)
    if direction == "in":
        df = df.filter(pl.col("receiver_account") == account)
        cp, amt = "sender_account", "amount_received"
    else:
        df = df.filter(pl.col("sender_account") == account)
        cp, amt = "receiver_account", "amount_paid"
    return _rows(
        df.group_by(pl.col(cp).alias("counterparty"))
        .agg(pl.len().alias("n_txns"), pl.col(amt).sum().alias("total_amount"),
             pl.col("ts").min().alias("first"), pl.col("ts").max().alias("last"))
        .with_columns(pl.col("first").cast(pl.Utf8), pl.col("last").cast(pl.Utf8))
        .sort("total_amount", descending=True)
        .head(top)
    )


def get_pass_through(account: str, window_hours: float = 48,
                     as_of: str | datetime | None = None,
                     limit: int = 50) -> list[dict]:
    """Inbound payments matched to outbound payments that follow within the
    window: the money-in/money-out pairs that make a mule or layering hop."""
    df = _cutoff(_involving(account), as_of, None).sort("ts")
    inc = df.filter(pl.col("receiver_account") == account)
    out = df.filter(pl.col("sender_account") == account)
    pairs = []
    out_rows = out.select("ts", "receiver_account", "amount_paid",
                          "payment_currency", "txn_id").iter_rows(named=True)
    out_list = list(out_rows)
    j = 0
    for r in inc.select("ts", "sender_account", "amount_received",
                        "receiving_currency", "txn_id").iter_rows(named=True):
        while j < len(out_list) and out_list[j]["ts"] < r["ts"]:
            j += 1
        for o in out_list[j:]:
            lag_h = (o["ts"] - r["ts"]).total_seconds() / 3600
            if lag_h > window_hours:
                break
            amt_in = r["amount_received"] or 0.0
            amt_out = o["amount_paid"] or 0.0
            pairs.append({
                "inbound_from": r["sender_account"], "inbound_amount": amt_in,
                "inbound_ts": r["ts"].isoformat(),
                "outbound_to": o["receiver_account"], "outbound_amount": amt_out,
                "outbound_ts": o["ts"].isoformat(),
                "lag_hours": round(lag_h, 2),
                "amount_ratio": round(min(amt_in, amt_out) / max(amt_in, amt_out), 3)
                if max(amt_in, amt_out) > 0 else 0.0,
            })
            if len(pairs) >= limit:
                return pairs
    return pairs


def get_shared_counterparties(a: str, b: str,
                              as_of: str | datetime | None = None) -> list[str]:
    """Accounts that transact with BOTH a and b (either direction)."""
    def cps(acct: str) -> set:
        df = _cutoff(_involving(acct), as_of, None)
        return (set(df.filter(pl.col("sender_account") == acct)["receiver_account"])
                | set(df.filter(pl.col("receiver_account") == acct)["sender_account"])
                ) - {acct}
    return sorted(cps(a) & cps(b))


def trace_funds(account: str, hops: int = 2, direction: str = "out",
                as_of: str | datetime | None = None, days: float = 14,
                max_edges_per_node: int = 15, min_amount: float = 0.0) -> dict:
    """Follow the money N hops. direction='out' follows where funds went,
    'in' follows where they came from. Flags cycles (money returning to a
    visited account). Per-node fan is capped so hub accounts stay readable --
    the cap keeps the LARGEST edges, and reports how many were dropped."""
    df = _cutoff(_trans(), as_of, days)
    src, dst, amt = (("sender_account", "receiver_account", "amount_paid")
                     if direction == "out"
                     else ("receiver_account", "sender_account", "amount_received"))
    nodes: dict[str, int] = {account: 0}
    edges: list[dict] = []
    cycles: list[str] = []
    truncated: dict[str, int] = {}
    frontier = [account]
    for hop in range(1, hops + 1):
        nxt = []
        for node in frontier:
            mine = (df.filter((pl.col(src) == node)
                              & (pl.col(amt) >= min_amount))
                    .sort(amt, descending=True))
            if mine.height > max_edges_per_node:
                truncated[node] = mine.height - max_edges_per_node
                mine = mine.head(max_edges_per_node)
            for r in mine.iter_rows(named=True):
                other = r[dst]
                edges.append({
                    "from" if direction == "out" else "to": node,
                    "to" if direction == "out" else "from": other,
                    "amount": r[amt], "currency": r["payment_currency"],
                    "ts": r["ts"].isoformat(), "hop": hop,
                })
                if other in nodes:
                    if other == account and node != account:
                        cycles.append(node)
                else:
                    nodes[other] = hop
                    nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return {
        "root": account, "direction": direction, "hops": hops,
        "nodes": [{"account": a, "hop": h} for a, h in nodes.items()],
        "edges": edges,
        "cycles_back_to_root_via": sorted(set(cycles)),
        "truncated_nodes": truncated,
    }


def get_prior_alerts(account: str,
                     as_of: str | datetime | None = None) -> list[dict]:
    """Earlier alerts on the same subject account (from the shipped stream).
    Strictly BEFORE as_of -- the triggering alert itself is not 'prior'."""
    if _ALERTS is None:
        return []
    df = _ALERTS.filter(pl.col("subject_account") == account)
    if as_of is not None:
        if isinstance(as_of, str):
            as_of = datetime.fromisoformat(as_of)
        df = df.filter(pl.col("ts") < as_of)
    return _rows(df.select("txn_id", "ts", "subject_account", "subject_side",
                           "rules_fired", "score").sort("ts"))


def get_account_features(account: str,
                         as_of: str | datetime | None = None) -> dict:
    """The feature store's 'shape' summary for ANY account, as of `as_of` --
    the same precomputed signals that flagged the alert, for any account in
    the graph (its counterparties, the accounts funds trace to, etc.). Lets
    the agent read a neighbour's mule-shape in one call instead of re-deriving
    it. Snapshotted from the account's latest event before as_of; no leakage."""
    if _FEAT is None:
        return {"account": account,
                "error": "feature store not loaded (init with features=True)"}
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of)

    def latest(side: str) -> dict | None:
        df = _FEAT.filter(pl.col(f"{side}_account") == account)
        if as_of is not None:
            df = df.filter(pl.col("ts") < as_of)
        df = df.sort("ts").tail(1)
        return df.row(0, named=True) if df.height else None

    s = latest("sender")       # this account acting as SENDER
    r = latest("receiver")     # this account acting as RECEIVER
    if s is None and r is None:
        return {"account": account, "seen_before_alert": False,
                "note": "no activity before the alert -- a brand-new account"}

    def g(row, key):
        return row.get(key) if row else None

    days_last = [d for d in (g(s, "days_since_last_out"),
                             g(r, "days_since_last_in")) if d is not None]
    return {
        "account": account,
        "as_of": (s or r)["ts"].isoformat(),
        # collector vs sprayer vs balanced -- the core mule shape
        "collect_ratio": g(r, "collect_ratio"),      # >4 = collector (mule-like)
        "spray_ratio": g(s, "spray_ratio"),          # >4.5 = sprayer (mule-like)
        "passthrough_ratio": g(s, "passthrough_ratio"),  # >0.9 = money passes through
        # counterparty structure
        "distinct_senders": g(r, "r_fan_in"),
        "distinct_receivers": g(s, "fan_out"),
        # are the relationships brand new? (~1.0 = fan assembled in last 24h)
        "new_sender_share_24h": g(r, "r_burst_in"),
        "new_receiver_share_24h": g(s, "burst_out"),
        # velocity
        "payments_in_24h": g(r, "r_n_in_1d"),
        "payments_out_24h": g(s, "n_out_1d"),
        "days_since_last_activity": min(days_last) if days_last else None,
    }

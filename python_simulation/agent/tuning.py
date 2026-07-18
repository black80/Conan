"""MODEL TUNING: severe failures -> exemplar cards -> re-run and diff.

"Tuning" here is the honest kind available for an API model (REQUIREMENTS.md
F2, D10): human-labeled severe failures become contrastive few-shot cards
appended to the agent's system prompt. Cards are built by a deterministic
template from facts an analyst can see (alert features + the agent's own filed
evidence + the human label) -- ground truth never enters. Leave-one-out is
automatic: a card is never shown to the case it came from.

The threshold lever (tau re-sweep) activates only at n>=10 labels; below that
it reports "insufficient labels" instead of pretending (n=30 was proven noise
in this project; n=3 would be theater).
"""

from __future__ import annotations

from datetime import datetime, timezone

import store
from loop import AGREES_WITH

MAX_CARDS = 4
MIN_LABELS_FOR_TAU = 10

# which alert features make a card's "shape" line, per subject side
_SIDE_FEATURES = {
    "receiver": ["r_fan_in", "collect_ratio", "r_fs_in_cnt_1d", "r_burst_in",
                 "passthrough_ratio"],
    "sender": ["fan_out", "spray_ratio", "n_out_1d", "burst_out",
               "passthrough_ratio"],
}

_LESSON = {
    "missed_mule": ("the benign story did not survive the label -- when the "
                    "structural signals conflict with a plausible business "
                    "narrative, verify the structure with a trace before "
                    "approving"),
    "false_alarm": ("the incriminating signals had an innocent explanation -- "
                    "a traced structure must rule the benign explanation out, "
                    "not merely look suspicious"),
}


def failures(severe_only: bool = False) -> list[dict]:
    """Every case where the human label DISAGREED with the agent, joined with
    the stored case and any rerun result. Severe ones (agent approved a mule /
    blocked a normal, D10) are flagged -- only those feed exemplar building;
    the tuning UI lists them all."""
    cases = store.latest_by(store.CASES, "case_id")
    reruns = store.latest_by(store.RERUNS, "case_id")
    out = []
    # one row per case: the LATEST label wins (labels are append-only and the
    # UI can re-label; without this a case is reviewed once per click)
    for lab in store.latest_by(store.LABELS, "case_id").values():
        if not lab.get("disagreement"):
            continue
        if severe_only and not lab.get("severe"):
            continue
        case = cases.get(lab["case_id"])
        if case is None:
            continue
        rr = reruns.get(lab["case_id"])
        out.append({
            "case_id": lab["case_id"],
            "alert_id": lab["alert_id"],
            "old_recommendation": lab["agent_recommendation"],
            "old_confidence": lab["agent_confidence"],
            "human_label": lab["label"],
            "severe": bool(lab.get("severe")),
            "failure_type": lab.get("failure_type") or "mild_disagreement",
            "typology": case.get("typology"),          # agent-asserted (D6)
            "labeled_by": lab.get("labeled_by"),
            "rerun_status": (rr or {}).get("outcome"),
            "data_ceiling": bool(rr and rr.get("outcome") == "still_wrong"
                                 and rr.get("attempt", 1) >= 2),
        })
    return out


def _card(fail: dict, case: dict) -> str:
    """Deterministic contrastive card from analyst-visible facts only."""
    alert = case["alert"]
    side = alert.get("subject_side", "sender")
    feats = alert.get("features", {})
    shape = ", ".join(f"{k}={feats[k]:.3g}" for k in _SIDE_FEATURES[side]
                      if feats.get(k) is not None)[:160]
    ledger = case.get("evidence") or []
    misread = next((e["value"] for e in ledger if e.get("supports") == "legit"),
                   "a benign-looking activity pattern")
    verdict = ("a MULE" if fail["human_label"] == "mule"
               else "LEGITIMATE")
    return (f"A flagged {side} account with [{shape}] was recommended "
            f"'{fail['old_recommendation']}' (confidence "
            f"{fail['old_confidence']:.2f}), citing: \"{str(misread)[:140]}\". "
            f"The analyst confirmed it was {verdict}. Lesson: "
            f"{_LESSON[fail['failure_type']]}.")


def build() -> dict:
    """Compile up to MAX_CARDS exemplar cards into out/exemplars.json.
    Returns the build summary (also what the UI's training sequence shows)."""
    cases = store.latest_by(store.CASES, "case_id")
    ranked = sorted(failures(severe_only=True), key=lambda f: -f["old_confidence"])
    cards, seen = [], set()
    for f in ranked:
        key = (f["failure_type"], f["typology"])
        if key in seen or f["data_ceiling"]:
            continue
        seen.add(key)
        cards.append({"case_id": f["case_id"], "alert_id": f["alert_id"],
                      "failure_type": f["failure_type"],
                      "text": _card(f, cases[f["case_id"]])})
        if len(cards) >= MAX_CARDS:
            break

    n_labels = len(store.read_all(store.LABELS))
    prev = store.read_json(store.EXEMPLARS, {"version": 0})
    doc = {
        "version": prev.get("version", 0) + 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_labels": n_labels,
        "tau": {"changed": False,
                "note": (f"threshold re-sweep needs >= {MIN_LABELS_FOR_TAU} "
                         f"labels (have {n_labels}); prompt exemplars only"
                         if n_labels < MIN_LABELS_FOR_TAU else
                         "run eval/sweep.py over accumulated labels")},
        "cards": cards,
    }
    store.write_json(store.EXEMPLARS, doc)
    return doc


def prompt_block(exemplars: dict, exclude_alert_id: str | None = None) -> str:
    """The system-prompt addendum. Leave-one-out: never show a case its own
    card. Empty string when there is nothing to inject."""
    cards = [c for c in exemplars.get("cards", [])
             if c["alert_id"] != exclude_alert_id]
    if not cards:
        return ""
    lines = "\n".join(f"{i + 1}. {c['text']}" for i, c in enumerate(cards))
    return ("\n\nPRIOR ANALYST CORRECTIONS -- real cases this desk got wrong, "
            "confirmed by a human analyst. Learn the PATTERNS, not the "
            "accounts; the current case must still be judged on its own "
            "evidence ledger:\n" + lines)


def outcome(old_recommendation: str, new_recommendation: str,
            human_label: str) -> str:
    """Four-way rerun outcome. `regressed` (was right, now wrong) is a real
    tuning risk -- it is displayed, never hidden."""
    target = AGREES_WITH.get(human_label)
    old_ok, new_ok = old_recommendation == target, new_recommendation == target
    if new_ok:
        return "flipped_correct" if not old_ok else "still_correct"
    return "still_wrong" if not old_ok else "regressed"


# ---------------------------------------------------------------- self-review
# The agent reviews its OWN disagreed cases: per-case post-mortem (streamed
# live), then one synthesis distilling error patterns + the correction cards
# that will change its future reasoning. Nothing installs until a human
# approves (the /api/tuning/approve endpoint writes exemplars.json).

REFLECT_SYSTEM = """You are the AML investigation agent. You investigated the case \
below and filed the report shown. A human analyst has since given the case its \
final label, and it DISAGREES with your recommendation.

Write a 2-4 sentence first-person post-mortem: (1) what you specifically \
misread or mis-weighted in your evidence ledger, and (2) why that reasoning \
led you astray. Cite the actual evidence lines and feature values. Concrete \
and unsparing -- a post-mortem, not an apology. Plain prose only: no markdown \
headers, no bold, no bullet lists."""

SYNTH_TOOL = {
    "name": "submit_adaptation",
    "description": "File the distilled adaptation from your post-mortems.",
    "input_schema": {
        "type": "object",
        "properties": {
            "error_patterns": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {"type": "string"},
                "description": "short bullets naming the recurring error "
                               "patterns across your mistakes"},
            "cards": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "text": {"type": "string",
                                 "description": "<=90 words. A correction card "
                                 "appended to your future system prompt: the "
                                 "feature shape that fooled you, what you "
                                 "concluded, what the analyst confirmed, and "
                                 "the lesson. Patterns, not account numbers."},
                    },
                    "required": ["case_id", "text"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string",
                        "description": "2-3 sentences, first person: how your "
                                       "reasoning will change if these "
                                       "corrections are installed"},
        },
        "required": ["error_patterns", "cards", "summary"],
        "additionalProperties": False,
    },
}

SYNTH_SYSTEM = """You are the AML investigation agent, concluding a review of your own \
mistakes. Below are your per-case post-mortems. Distill them: the recurring \
error patterns, up to 4 correction cards (the most instructive cases -- these \
are appended verbatim to your future system prompt under 'PRIOR ANALYST \
CORRECTIONS'), and a summary of how your reasoning will change. The human \
analyst will read all of it and decide whether to install the change. Call \
submit_adaptation exactly once."""


def _review_packet(fail: dict, case: dict) -> dict:
    """What the agent sees about one of its disagreed cases -- its own filed
    report + the human label. No ground truth beyond the label."""
    alert = case["alert"]
    side = alert.get("subject_side", "sender")
    feats = alert.get("features", {})
    return {
        "case_id": fail["case_id"],
        "your_recommendation": fail["old_recommendation"],
        "your_confidence": fail["old_confidence"],
        "analyst_label": fail["human_label"],
        "your_summary": case.get("summary"),
        "your_evidence_ledger": case.get("evidence"),
        "subject_side": side,
        "key_features": {k: round(feats[k], 3) for k in _SIDE_FEATURES[side]
                         if feats.get(k) is not None},
    }


def self_review(fails: list[dict], cases: dict, client, model: str,
                emit) -> dict:
    """Per-case post-mortems (text streamed live via emit) + one synthesis.
    Returns the proposed adaptation doc; installing it is the human's call."""
    import json as _json
    reflections = []
    for f in fails:
        case = cases.get(f["case_id"])
        if case is None:
            continue
        emit({"type": "reflect_start", "case_id": f["case_id"],
              "old": {"recommendation": f["old_recommendation"],
                      "confidence": f["old_confidence"]},
              "label": f["human_label"]})
        packet = _json.dumps(_review_packet(f, case), indent=1, default=str)
        full, buf = "", ""
        with client.messages.stream(
                model=model, max_tokens=600, system=REFLECT_SYSTEM,
                messages=[{"role": "user", "content": packet}]) as st:
            for t in st.text_stream:
                full += t
                buf += t
                if len(buf) >= 40:
                    emit({"type": "reflect_delta", "case_id": f["case_id"],
                          "text": buf})
                    buf = ""
        if buf:
            emit({"type": "reflect_delta", "case_id": f["case_id"], "text": buf})
        emit({"type": "reflect_done", "case_id": f["case_id"]})
        reflections.append({"case_id": f["case_id"],
                            "label": f["human_label"],
                            "post_mortem": full})

    emit({"type": "synthesizing",
          "label": "Distilling error patterns and drafting the reasoning change"})
    response = client.messages.create(
        model=model, max_tokens=2_048, system=SYNTH_SYSTEM,
        tools=[SYNTH_TOOL],
        tool_choice={"type": "tool", "name": "submit_adaptation"},
        messages=[{"role": "user",
                   "content": _json.dumps(reflections, indent=1, default=str)}],
    )
    tu = next(b for b in response.content if b.type == "tool_use")
    doc = dict(tu.input)
    doc["reflections"] = reflections
    return doc

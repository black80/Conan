"""THE AGENT: investigate(alert) -> Case.

The model works one alert the way a human analyst would: pulls the subject
account's profile and history, checks counterparties and pass-through flows,
traces funds through the graph, then files a structured case with a
recommendation. Every tool call is pinned to as_of = the alert's timestamp,
so the agent only sees what existed when the alert fired.

The primary job (proof #1) is TRIAGE: the rule layer's queue is ~90% false
positives; the agent must approve (auto-close) the noise and escalate/block
the real cases. Ground-truth labels exist nowhere in this module or in the
tool layer -- the agent earns its numbers blind.

Two providers, one harness (same prompt, tools, anchoring, Case output):
  - Claude models (default): Anthropic API, ANTHROPIC_API_KEY
  - anything else (e.g. "openai/gpt-oss-120b"): OpenAI-compatible API --
    Groq by default (GROQ_API_KEY; free tier at console.groq.com), or set
    OPENAI_BASE_URL/OPENAI_API_KEY for vLLM/Ollama/another host.

    from agent.agent import investigate
    case = investigate(alert_dict)          # ~6-10 tool calls, seconds
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import anthropic

from agent import tools

MODEL = "claude-opus-4-8"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_TURNS = 12
# The action is decided in code from the model's probabilities, NOT by the
# model (research: models don't act on their own stated confidence). Tunable
# on labeled data -- the harness may see labels; the agent never does.
TAU_APPROVE = 0.70     # approve (auto-close) if P(legitimate) >= this
TAU_BLOCK = 0.75       # block if P(laundering) >= this AND a trace confirmed it
TOOL_RESULT_CHAR_CAP = 9_000     # keep hub-sized tool outputs from flooding context
# Groq's free tier is 8k tokens/min and counts max_tokens toward it, so the
# OpenAI path runs leaner: smaller completion budget + tighter tool outputs.
# gpt-oss is a reasoning model, so leave room for its reasoning tokens.
OPENAI_MAX_TOKENS = 2_500
OPENAI_TOOL_CHAR_CAP = 2_200

_SYSTEM_PREFIX = """You are a senior anti-money-laundering (AML) analyst at a bank. A rule \
engine has flagged an account; your job is to investigate it and file a case.

CRITICAL FRAMING: every account you see is ALREADY anomalous by the rule \
engine's standards -- high fan-in, high fan-out, big volume, or fast \
throughput is exactly WHY it was flagged. Those raw signals are therefore \
NOT evidence of laundering here -- they are the price of admission. Roughly 9 \
in 10 of these flagged accounts are legitimate businesses (busy merchants, \
payroll hubs, corporate treasury, exchanges) that are simply active. Your job \
is to find the ~1 in 10 that is actually laundering, hidden among nine busy \
but innocent accounts.

DEFAULT TO APPROVE. Start from the presumption that the account is a \
legitimate-but-busy business and look for evidence that OVERTURNS that \
presumption. "Many counterparties" or "large volume" does not overturn it -- \
that is what legitimate hubs look like too. If after investigating you have \
not found positive, specific evidence of laundering, APPROVE (auto-close). \
Blocking a legitimate business is a serious error; do not do it on suspicion \
alone.

INVESTIGATE THE SUBJECT. The alert names `subject_account` -- the account the \
rules indict (which may be the RECEIVER of the alerted payment, not the \
sender). Investigate that account: its history, counterparties, where money \
came from and went next. All tool data is as-of the alert time; amounts are \
in each transaction's native currency (do not compare raw amounts across \
currencies).

LAUNDERING TYPOLOGIES you may find (money launderers move stolen funds through \
networks of mule accounts in recognizable graph shapes):
- FAN-IN: many accounts send to one collector; little or nothing flows out yet.
- FAN-OUT: one account sprays funds to many receivers, often new counterparties.
- GATHER-SCATTER: funds collect into an account, then spray out to many others.
- SCATTER-GATHER: one source sprays to several middles who forward to one destination.
- CYCLE: money moves through a chain and returns to (or near) its origin.
- STACK: money hops through a chain of pass-through accounts, amounts nearly preserved.
- BIPARTITE: a set of senders each pay a set of receivers, forming a dense two-layer web.
- RANDOM: transfers between conspirators with no clean shape -- hardest to see.

GATHER FIRST, JUDGE SECOND. Do NOT narrate a conclusion while you call tools. \
First gather evidence to answer the four questions below; only after you have \
the facts do you decide. Volume and counterparty counts feel suspicious but \
are NOT discriminating (every flagged account has them) -- ignore them as \
evidence. Answer each question YES / NO / UNCLEAR from what the tools show:

  Q1 NEW RELATIONSHIPS? Are the subject's active counterparties almost all \
  brand-new (first contact inside the alert window), versus established and \
  repeating? [get_counterparties in and out -- read first/last contact \
  timestamps and n_txns; a legit hub reuses counterparties over time.]

  Q2 PASS-THROUGH? Does inbound money leave within hours at nearly the same \
  amount, in few transactions? [get_pass_through -- count pairs with ratio \
  > 0.8 and short lag. A real business KEEPS and MIXES money; it does not \
  forward ~100% of each payment intact.]

  Q3 IN A STRUCTURE? Does trace_funds show the subject inside a CHAIN of \
  similar accounts, a CYCLE back toward origin, or a tight scatter/gather \
  web -- versus an isolated busy node with unrelated neighbours? [trace_funds \
  out and/or in, 2 hops.] Then CHECK THE NEIGHBOURS' SHAPE with \
  get_account_features on a few key counterparties / trace targets: if they \
  are themselves mule-shaped (high collect/spray/passthrough ratios, \
  relationships all brand-new), that confirms a ring and supports BLOCK; if \
  they look like ordinary businesses, that argues the subject is a false \
  positive and supports APPROVE.

  Q4 LOOKS LIKE A REAL BUSINESS? Does get_account_profile show an entity and \
  activity consistent with commerce -- varied retail-sized amounts, a stable \
  book of repeating counterparties, activity spread over time, both spending \
  AND receiving? [get_account_profile + histories.]

BUILD A TWO-SIDED LEDGER. For your verdict you must weigh BOTH:
- SUPPORTING evidence (argues the account IS laundering), and
- CONTRADICTING / exculpatory evidence (argues it is a legitimate business).
You must actively look for the CONTRADICTING side -- established relationships, \
retained/mixed funds, unrelated neighbours, varied amounts, a real entity. A \
verdict that lists only incriminating facts and ignores the exculpatory ones \
is INVALID; a mule and a busy merchant look identical until you check the \
contradicting column. Your `evidence` output must include the legit/neutral \
facts you found, not just the fraud ones."""


# ---- v4 decision block (probabilities; action decided in the harness)
_DECISION_V4 = """YOU DO NOT CHOOSE THE ACTION. You report two calibrated probabilities and the \
evidence; the system decides approve / escalate / block from your numbers and \
whether you traced a structure. So do NOT agonize over "should I escalate or \
approve" -- just report honest, well-calibrated probabilities and let the \
threshold do its job. Report:

- p_legitimate (0-1): your calibrated probability the account is a legitimate \
  business. Anchor to the base rate: ~90 of every 100 alerts are legitimate, \
  so absent specific laundering evidence this should be HIGH (0.8-0.95). \
  "Busy" is not "suspicious" -- high volume / many counterparties / big \
  amounts are normal for legitimate merchants, payroll, marketplaces.
- p_laundering (0-1): your calibrated probability it IS laundering. This \
  should be high ONLY when you traced an actual structure (Q2 pass-through \
  plus Q1 new relationships or Q3 chain/cycle). Raw volume never raises it.
- (the two need not sum to 1: if genuinely torn, put both near 0.5.)

CALIBRATION -- these numbers must track reality, they are not vibes: use 0.9+ \
only when you'd stake real money on being right; use the middle of the range \
when torn (that becomes an escalate, which is fine). Do not default everything \
to 0.9. A verdict resting on a plausible story rather than cited tool facts is \
a middling probability, not a confident one.

traced_structure (true/false): true ONLY if a COMPLETED trace_funds or \
pass_through result actually showed the laundering structure (a chain, a \
cycle, a fan of forwarding mule relationships). Never true on volume or the \
alerted transaction alone. This GATES the block action -- no trace, no block.

benign_reason: if you lean legitimate, name WHY (this forces commitment \
instead of a lazy hedge): "benign_positive" (real, legitimate business \
activity), "fp_logic" (the rule fired on a pattern that is not a laundering \
typology at all), "fp_data" (an alert artifact / self-transfer / data quirk), \
or "undetermined" (you genuinely cannot tell -- only then is a hedge honest).

COUNTERFACTUAL DISCIPLINE: before assigning a HIGH p_laundering, ask "what \
legitimate business would produce exactly this pattern, and did my completed \
trace actually rule it out?" If an innocent explanation survives, your \
p_laundering is not high. Symmetrically, do not talk yourself into a HIGH \
p_legitimate by inventing an unverified "FX house / processor" story for a \
verified pass-through with all-new counterparties -- that is a hypothesis, not \
evidence.

METHOD: 4-8 tool calls. Typically get_account_profile + get_counterparties \
(Q1, Q4), get_pass_through (Q2), trace_funds (Q3), get_account_features on a \
couple of neighbours. Answer the four questions, build the two-sided ledger, \
report your probabilities.

VERDICT -- finish by calling submit_case with:
- p_legitimate, p_laundering: calibrated as above.
- traced_structure: true/false as above.
- benign_reason: one of the four above.
- typology: the shape you identified if laundering is plausible (FAN-IN, \
STACK, CYCLE, ...), else "NONE".
- summary: 3-5 sentences, stating your Q1-Q4 findings and why your \
probabilities are what they are.
- evidence: the specific cited facts -- BOTH the fraud-supporting AND the \
legit/neutral (contradicting) ones you found.
You MUST finish by calling submit_case."""


# ---- v3 decision block (model picks the action directly) -- frozen for the
# controlled A/B: v3 == the prompt that scored 86% recall / 41% specificity on
# Haiku (n=100). Same investigation prefix; only the decision mechanism differs.
_DECISION_V3 = """DECISION RULE (apply mechanically):
- BLOCK only when Q2=YES AND (Q1=YES OR Q3=YES) AND you have a COMPLETED \
trace_funds or pass_through result that actually shows the structure. You may \
never block on the alerted transaction alone or on volume alone.
- APPROVE when Q2=NO OR Q4=YES: the account keeps/mixes its money or behaves \
like a real business. This is the DEFAULT and correct ~9 times in 10.
- ESCALATE when evidence is mixed or UNCLEAR. ESCALATE IS THE UNCERTAINTY \
SINK -- when supporting and contradicting evidence are both plausible, \
escalate rather than guess.

COUNTERFACTUAL CHECK before a BLOCK: ask "is there one plausible benign \
explanation for my strongest supporting fact that I did not rule out with the \
tools?" If yes, downgrade to ESCALATE. Do not do the reverse error either: do \
not approve a verified pass-through with all-new counterparties by inventing \
an unverified "FX house" story.

CONFIDENCE CALIBRATION: confidence > 0.9 only for verdicts resting on TRACED \
structure (block) or VERIFIED legitimacy (approve).

VERDICT -- finish by calling submit_case with: recommendation \
(approve|escalate|block), confidence (0-1), typology (or "NONE"), summary \
(3-5 sentences), and evidence (BOTH fraud-supporting and legit/neutral facts).
You MUST finish by calling submit_case."""

SYSTEM_V4 = _SYSTEM_PREFIX + "\n\n" + _DECISION_V4
SYSTEM_V3 = _SYSTEM_PREFIX + "\n\n" + _DECISION_V3
SYSTEM = SYSTEM_V4                      # default

# ------------------------------------------------------------ tool definitions

TOOL_DEFS = [
    {
        "name": "get_account_profile",
        "description": "The account's identity (bank, owning entity, sibling "
                       "accounts of the same entity) and lifetime activity totals: "
                       "counts, distinct counterparties, per-currency sums, formats "
                       "used, first/last seen. Call this first for the subject.",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "get_account_history",
        "description": "The account's raw transactions in the last `days` before "
                       "the alert, newest first. Use a small limit (20-40) -- you "
                       "need the pattern, not every row.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "days": {"type": "number", "description": "lookback window, default 7"},
                "limit": {"type": "integer", "description": "max rows, default 30"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "get_counterparties",
        "description": "Who the account transacts with in the window: one row per "
                       "counterparty with txn count, total amount, first and last "
                       "contact. direction 'in' = who pays the account, 'out' = "
                       "who it pays. First/last timestamps reveal whether "
                       "relationships are established or brand new.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "direction": {"type": "string", "enum": ["in", "out"]},
                "days": {"type": "number", "description": "default 7"},
                "top": {"type": "integer", "description": "max rows, default 20"},
            },
            "required": ["account", "direction"],
        },
    },
    {
        "name": "get_pass_through",
        "description": "Money-in matched to money-out: inbound payments paired "
                       "with outbound payments that follow within `window_hours`, "
                       "with the lag and amount ratio. High-ratio short-lag pairs "
                       "are the signature of a mule or layering hop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "window_hours": {"type": "number", "description": "default 48"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "get_shared_counterparties",
        "description": "Accounts that transact with BOTH a and b. Use to test "
                       "whether two suspicious accounts belong to one ring.",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "trace_funds",
        "description": "Follow the money N hops through the graph. "
                       "direction 'out' = where funds went next, 'in' = where they "
                       "came from. Returns nodes, edges (largest first, capped per "
                       "node), and flags cycles back to the root. THE tool for "
                       "STACK / CYCLE / SCATTER-GATHER shapes -- a rule engine "
                       "cannot do this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "hops": {"type": "integer", "description": "1-3, default 2"},
                "direction": {"type": "string", "enum": ["out", "in"]},
                "min_amount": {"type": "number",
                               "description": "ignore edges below this, default 0"},
            },
            "required": ["account", "direction"],
        },
    },
    {
        "name": "get_prior_alerts",
        "description": "Earlier alerts on the same subject account from the "
                       "detection stream (before this one).",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "get_account_features",
        "description": "The feature-store 'shape' of ANY account in one call: "
                       "collect_ratio / spray_ratio (is it a collector or "
                       "sprayer?), passthrough_ratio (does money pass straight "
                       "through?), distinct senders/receivers, new-counterparty "
                       "share in 24h (~1.0 = relationships all brand new), and "
                       "recency. Use it on the SUBJECT's neighbours -- the "
                       "counterparties feeding it and the accounts funds trace "
                       "to. If those neighbours are themselves mule-shaped "
                       "(high ratios, all-new relationships, money passing "
                       "through), that is strong confirming evidence of a ring; "
                       "if they look like ordinary businesses, that argues the "
                       "subject is legit. Much cheaper than profiling each "
                       "neighbour with the other tools.",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "submit_case",
        "description": "File the final case. Call exactly once, when your "
                       "investigation is complete. Fill the fields your "
                       "instructions asked for.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string",
                            "description": "3-5 sentences, findings first"},
                # v4 fields (probabilities; action decided by the harness)
                "p_legitimate": {"type": "number",
                                 "description": "calibrated P(legitimate business), 0-1"},
                "p_laundering": {"type": "number",
                                 "description": "calibrated P(laundering), 0-1"},
                "traced_structure": {
                    "type": "boolean",
                    "description": "true only if a COMPLETED trace showed the "
                                   "laundering structure -- gates block"},
                "benign_reason": {
                    "type": "string",
                    "enum": ["benign_positive", "fp_logic", "fp_data",
                             "undetermined", "none"]},
                # v3 fields (model picks the action)
                "recommendation": {"type": "string",
                                   "enum": ["approve", "escalate", "block"]},
                "confidence": {"type": "number"},
                "typology": {"type": "string",
                             "enum": ["FAN-IN", "FAN-OUT", "GATHER-SCATTER",
                                      "SCATTER-GATHER", "CYCLE", "STACK",
                                      "BIPARTITE", "RANDOM", "NONE"]},
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "supports": {"type": "string",
                                         "enum": ["fraud", "legit", "neutral"]},
                        },
                        "required": ["label", "value", "supports"],
                        "additionalProperties": False,
                    },
                },
            },
            # unified schema across prompt versions -> only the common fields
            # are required; each prompt fills its own decision fields
            "required": ["summary", "typology", "evidence"],
            "additionalProperties": False,
        },
    },
]

_TOOL_FNS = {
    "get_account_profile": lambda inp, as_of: tools.get_account_profile(inp["account"]),
    "get_account_history": lambda inp, as_of: tools.get_account_history(
        inp["account"], days=inp.get("days", 7), as_of=as_of,
        limit=min(int(inp.get("limit", 30)), 60)),
    "get_counterparties": lambda inp, as_of: tools.get_counterparties(
        inp["account"], direction=inp["direction"], days=inp.get("days", 7),
        as_of=as_of, top=min(int(inp.get("top", 20)), 40)),
    "get_pass_through": lambda inp, as_of: tools.get_pass_through(
        inp["account"], window_hours=inp.get("window_hours", 48), as_of=as_of),
    "get_shared_counterparties": lambda inp, as_of: tools.get_shared_counterparties(
        inp["a"], inp["b"], as_of=as_of),
    "trace_funds": lambda inp, as_of: tools.trace_funds(
        inp["account"], hops=min(int(inp.get("hops", 2)), 3),
        direction=inp["direction"], as_of=as_of,
        min_amount=inp.get("min_amount", 0.0)),
    "get_prior_alerts": lambda inp, as_of: tools.get_prior_alerts(
        inp["account"], as_of=as_of),
    "get_account_features": lambda inp, as_of: tools.get_account_features(
        inp["account"], as_of=as_of),
}


def humanize(name: str, inp: dict) -> str:
    """A one-line, analyst-readable description of a tool call -- used for the
    live activity stream in the UI. Pure presentation; no effect on the loop."""
    a = inp.get("account", "")
    if name == "get_account_profile":
        return "Pulling the account's identity and lifetime activity"
    if name == "get_account_history":
        return f"Reading the last {int(inp.get('days', 7))} days of transactions"
    if name == "get_counterparties":
        d = inp.get("direction", "in")
        who = "who pays this account" if d == "in" else "who this account pays"
        return f"Listing counterparties ({who})"
    if name == "get_pass_through":
        return "Matching money-in to money-out (pass-through check)"
    if name == "get_shared_counterparties":
        return "Checking whether two accounts share counterparties"
    if name == "trace_funds":
        d = inp.get("direction", "out")
        where = "where the money went next" if d == "out" else "where the money came from"
        return f"Tracing funds {int(inp.get('hops', 2))} hops -- {where}"
    if name == "get_prior_alerts":
        return "Checking for earlier alerts on this account"
    if name == "get_account_features":
        return f"Profiling neighbour {a}'s shape (collector / sprayer / pass-through)"
    if name == "submit_case":
        return "Weighing the evidence and filing the case"
    return name


def _emit(on_step, **event) -> None:
    """Push one live event to the optional callback; never let a UI callback
    error break the investigation."""
    if on_step is None:
        return
    try:
        on_step(event)
    except Exception:                                          # noqa: BLE001
        pass


def _run_tool(name: str, inp: dict, as_of: str,
              cap: int = TOOL_RESULT_CHAR_CAP) -> tuple[str, bool]:
    """Execute one tool. Returns (json_result, is_error)."""
    try:
        result = _TOOL_FNS[name](inp, as_of)
        text = json.dumps(result, default=str)
        if len(text) > cap:
            text = text[:cap] + '... [truncated -- narrow your query]'
        return text, False
    except Exception as e:                                    # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}", True


# the fallback verdict when the loop ends without a filed case: max uncertainty
_TIMEOUT_VERDICT = {
    "summary": "Investigation did not reach a verdict within the turn limit; "
               "escalating for human review.",
    "p_legitimate": 0.5, "p_laundering": 0.5, "traced_structure": False,
    "benign_reason": "undetermined", "typology": None, "evidence": [],
}
_KICKOFF = ("Investigate this alert and file a case.\n\n```json\n{}\n```")


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _decide(verdict: dict, trace: list[str], version: str) -> tuple[str, float]:
    """Map a filed verdict to an action.
    v4: decided HERE from probabilities (research: models don't act on their
        own confidence); block also requires a trace tool was actually run.
    v3: the model's own recommendation (bad/missing -> escalate)."""
    if version == "v3":
        rec = verdict.get("recommendation")
        if rec not in ("approve", "escalate", "block"):
            rec = "escalate"
        return rec, _f(verdict.get("confidence"))
    p_legit = _f(verdict.get("p_legitimate"))
    p_laund = _f(verdict.get("p_laundering"))
    ran_trace = any(t.startswith(("trace_funds", "get_pass_through")) for t in trace)
    traced = bool(verdict.get("traced_structure")) and ran_trace
    if p_laund >= TAU_BLOCK and traced:
        return "block", p_laund
    if p_legit >= TAU_APPROVE:
        return "approve", p_legit
    return "escalate", max(p_legit, p_laund)


def _build_case(alert: dict, verdict: dict, trace: list[str], model: str,
                usage: dict, version: str = "v4") -> dict:
    typology = verdict.get("typology")
    if typology in ("NONE", "", "null", "None"):
        typology = None
    recommendation, confidence = _decide(verdict, trace, version)
    return {
        "case_id": f"CS-{alert['alert_id'].removeprefix('AL-')}",
        "alert": alert,
        "summary": verdict.get("summary") or "(no summary filed)",
        "evidence": verdict.get("evidence") or [],
        "typology": typology,
        "recommendation": recommendation,
        "confidence": round(confidence, 3),
        "reasoning_trace": trace,
        "status": ("auto_closed" if recommendation == "approve"
                   else "needs_review"),
        "_meta": {
            "model": model,
            "prompt_version": version,
            "tool_calls": sum(1 for t in trace
                              if not t.startswith(("nudge", "MODEL"))),
            "p_legitimate": _f(verdict.get("p_legitimate")),
            "p_laundering": _f(verdict.get("p_laundering")),
            "traced_structure": bool(verdict.get("traced_structure")),
            "benign_reason": verdict.get("benign_reason"),
            "usage": usage,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------- Claude loop

def _investigate_anthropic(alert, model, as_of, trace, verbose, client, system,
                           on_step=None):
    client = client or anthropic.Anthropic()
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    # adaptive thinking is Opus/Sonnet 4.6+ / Fable only -- Haiku 4.5 rejects it
    extra = {} if "haiku" in model else {"thinking": {"type": "adaptive"}}
    messages = [{"role": "user",
                 "content": _KICKOFF.format(json.dumps(alert, indent=2, default=str))}]
    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model, max_tokens=8_192,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_DEFS, messages=messages, **extra,
        )
        for k in usage:
            usage[k] += getattr(response.usage, k, None) or 0
        if response.stop_reason == "refusal":
            trace.append("MODEL REFUSAL -- case escalated unreviewed")
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",
                             "content": "File your verdict now by calling submit_case."})
            trace.append("nudge: model ended turn without submit_case")
            continue
        results = []
        for tu in tool_uses:
            if tu.name == "submit_case":
                _emit(on_step, type="filing", label=humanize("submit_case", {}))
                trace.append(
                    f"submit_case(p_legit={tu.input.get('p_legitimate')}, "
                    f"p_laund={tu.input.get('p_laundering')}, "
                    f"traced={tu.input.get('traced_structure')}, "
                    f"typology={tu.input.get('typology')})")
                return tu.input, usage
            _emit(on_step, type="tool", name=tu.name,
                  label=humanize(tu.name, tu.input), args=dict(tu.input))
            out, is_err = _run_tool(tu.name, tu.input, as_of)
            arg_str = ", ".join(f"{k}={v}" for k, v in tu.input.items())
            trace.append(f"{tu.name}({arg_str}) -> {'ERROR: ' if is_err else ''}{out[:400]}")
            if verbose:
                print(f"    {tu.name}({arg_str})")
            _emit(on_step, type="tool_done", name=tu.name, is_error=is_err)
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": out, **({"is_error": True} if is_err else {})})
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})
    return None, usage


# ----------------------------------------------- OpenAI-compatible loop (Groq)

def _openai_tools() -> list[dict]:
    """Anthropic tool defs -> OpenAI function-tool defs."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in TOOL_DEFS]


def _openai_call(client, model_id, oa_tools, messages, trace, verbose,
                 retries: int = 6):
    """One chat.completions call, retrying rate-limit throttling with backoff.
    Groq's free tier (8k tokens/min) throttles a multi-turn loop; we wait it
    out rather than failing the case."""
    import openai
    for attempt in range(retries):
        try:
            return client.chat.completions.create(
                model=model_id, max_tokens=OPENAI_MAX_TOKENS, tools=oa_tools,
                tool_choice="auto", messages=messages,
            )
        except openai.RateLimitError as e:
            wait = getattr(getattr(e, "response", None), "headers", {}) or {}
            delay = float(wait.get("retry-after", 0)) or min(2 ** attempt, 30)
            if verbose:
                print(f"    rate-limited, waiting {delay:.0f}s")
            trace.append(f"rate-limited, waited {delay:.0f}s")
            time.sleep(delay)
    raise RuntimeError("exhausted rate-limit retries")


def _investigate_openai(alert, model, as_of, trace, verbose, client, system,
                        on_step=None):
    from openai import OpenAI
    if client is None:
        base_url = os.environ.get("OPENAI_BASE_URL", GROQ_BASE_URL)
        api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")
        client = OpenAI(base_url=base_url, api_key=api_key)
    # model id passes through verbatim -- Groq's gpt-oss id IS "openai/gpt-oss-120b"
    model_id = model
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    oa_tools = _openai_tools()
    messages = [{"role": "system", "content": system},
                {"role": "user",
                 "content": _KICKOFF.format(json.dumps(alert, indent=2, default=str))}]
    for _ in range(MAX_TURNS):
        response = _openai_call(client, model_id, oa_tools, messages, trace, verbose)
        u = response.usage
        if u:
            usage["input_tokens"] += u.prompt_tokens or 0
            usage["output_tokens"] += u.completion_tokens or 0
        msg = response.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({"role": "user",
                             "content": "File your verdict now by calling submit_case."})
            trace.append("nudge: model ended turn without submit_case")
            continue
        # mirror assistant turn (content may be None on pure tool calls)
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [c.model_dump() for c in calls]})
        for call in calls:
            name = call.function.name
            try:
                inp = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                inp = {}
            if name == "submit_case":
                _emit(on_step, type="filing", label=humanize("submit_case", {}))
                trace.append(f"submit_case(p_legit={inp.get('p_legitimate')}, "
                             f"p_laund={inp.get('p_laundering')}, "
                             f"traced={inp.get('traced_structure')}, "
                             f"typology={inp.get('typology')})")
                return inp, usage
            _emit(on_step, type="tool", name=name,
                  label=humanize(name, inp), args=dict(inp))
            out, is_err = _run_tool(name, inp, as_of, cap=OPENAI_TOOL_CHAR_CAP)
            arg_str = ", ".join(f"{k}={v}" for k, v in inp.items())
            trace.append(f"{name}({arg_str}) -> {'ERROR: ' if is_err else ''}{out[:400]}")
            if verbose:
                print(f"    {name}({arg_str})")
            _emit(on_step, type="tool_done", name=name, is_error=is_err)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
    return None, usage


# ------------------------------------------------------------------- the agent

def investigate(alert: dict, model: str = MODEL, client=None,
                verbose: bool = False, version: str = "v4", on_step=None,
                exemplars: dict | None = None) -> dict:
    """Run one investigation. Returns a Case-shaped dict (contracts.Case).

    model:   a Claude id (Anthropic API) or an "openai/"-prefixed id such as
             "openai/gpt-oss-120b" (OpenAI-compatible API, Groq by default).
    version: "v4" (probabilities, harness decides) or "v3" (model decides) --
             the controlled A/B knob; holds everything else fixed.
    on_step: optional callback(event: dict) fired live as the agent works --
             {type:"tool"|"tool_done"|"filing", label, name, args}. Used by the
             UI backend to stream the investigation; None in batch runs.
    exemplars: optional exemplars.json content (agent/tuning.py). Appends the
             "prior analyst corrections" block to the system prompt, with
             leave-one-out (this alert's own card is never shown). EXPLICIT
             opt-in only -- batch benchmarks that don't pass it are unaffected.
    """
    as_of = (
        None
        if alert.get("source") == "nfc"
        else alert["txn"]["timestamp"]
    )
    trace: list[str] = []
    system = SYSTEM_V3 if version == "v3" else SYSTEM_V4
    if exemplars:
        from agent.tuning import prompt_block
        block = prompt_block(exemplars, exclude_alert_id=alert["alert_id"])
        if block:
            system = system + block
            version = f"{version}+ex{exemplars.get('version', '?')}"
    # Claude models -> Anthropic API; everything else -> OpenAI-compatible (Groq).
    loop = (_investigate_anthropic if model.startswith("claude")
            else _investigate_openai)
    verdict, usage = loop(alert, model, as_of, trace, verbose, client, system,
                          on_step=on_step)
    if verdict is None:
        verdict = _TIMEOUT_VERDICT if version.startswith("v4") else {
            "summary": "No verdict within turn limit; escalating.",
            "recommendation": "escalate", "confidence": 0.0,
            "typology": None, "evidence": []}
    return _build_case(alert, verdict, trace, model, usage, version)

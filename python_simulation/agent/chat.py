"""Ask-the-agent: follow-up Q&A on a filed case (REQUIREMENTS.md US-1.3).

The investigator's question re-enters the REAL agent loop -- same 8 read-only
graph tools, every call still pinned as-of the alert timestamp, labels still
unreachable. The conversation is seeded with the alert and the filed Case, so
the agent answers from its own investigation first and digs (<=5 new tool
calls) only when the question needs new data. History is ephemeral: the
browser sends prior turns back with each question.

    answer = ask(alert, case, question, history, client=..., on_step=...)
"""

from __future__ import annotations

import json

import anthropic

from agent.agent import (MODEL, TOOL_DEFS, _emit, _run_tool, humanize)

MAX_TOOL_CALLS = 5
MAX_TURNS = 8

CHAT_SYSTEM = """You are the AML analyst agent that investigated this case; a human \
investigator is now asking you follow-up questions about it.

You are given the original alert and the case you filed (summary, evidence \
ledger, reasoning trace). Answer from that investigation first; call tools \
only when the question needs data you do not already have. All tool data is \
as-of the alert time. You have a budget of {budget} tool calls for this \
question -- spend them only if needed.

Answering rules:
- Cite specific tool-derived facts (amounts, counts, timestamps, accounts).
- Distinguish what you found during the filed investigation vs what you just \
retrieved now.
- You have NO access to ground-truth fraud labels; if asked whether the \
account is "really" laundering, explain what the evidence supports and say \
the final call belongs to the investigator.
- If the available data cannot answer the question, say exactly that.
- Be concise: a few sentences, findings first. Do not re-file the case."""

_SEED = """Here is the case under discussion.

ALERT:
```json
{alert}
```

FILED CASE (your prior investigation):
```json
{case}
```"""


def _chat_tools() -> list[dict]:
    return [t for t in TOOL_DEFS if t["name"] != "submit_case"]


def ask(alert: dict, case: dict, question: str, history: list[dict] | None = None,
        model: str = MODEL, client=None, on_step=None) -> str:
    """One follow-up exchange. Returns the agent's text answer."""
    client = client or anthropic.Anthropic()
    as_of = alert["txn"]["timestamp"]
    slim_case = {k: case[k] for k in
                 ("summary", "evidence", "typology", "recommendation",
                  "confidence", "reasoning_trace") if k in case}
    seed = _SEED.format(alert=json.dumps(alert, indent=2, default=str),
                        case=json.dumps(slim_case, indent=2, default=str))
    messages = [{"role": "user", "content": seed},
                {"role": "assistant",
                 "content": "Understood. I investigated this case; ask me anything about it."}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    system = CHAT_SYSTEM.format(budget=MAX_TOOL_CALLS)
    extra = {} if "haiku" in model else {"thinking": {"type": "adaptive"}}
    calls_left = MAX_TOOL_CALLS
    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model, max_tokens=4_096,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            tools=_chat_tools(), messages=messages, **extra,
        )
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or calls_left <= 0:
            text = "".join(b.text for b in response.content if b.type == "text")
            return text or "(no answer)"
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            calls_left -= 1
            _emit(on_step, type="tool", name=tu.name,
                  label=humanize(tu.name, tu.input), args=dict(tu.input))
            out, is_err = _run_tool(tu.name, tu.input, as_of)
            _emit(on_step, type="tool_done", name=tu.name, is_error=is_err)
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": out, **({"is_error": True} if is_err else {})})
        messages.append({"role": "user", "content": results})
    return "(ran out of turns before finishing the answer)"

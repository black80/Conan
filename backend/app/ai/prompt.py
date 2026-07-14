import json

SYSTEM_PROMPT = (
    "You are a fraud investigation analyst. You review a payment transaction that a "
    "rule engine flagged as suspicious, together with the customer's context, and "
    "produce a structured assessment for a human investigator. Be precise and "
    "conservative. Respond with ONLY a single JSON object, no markdown, matching "
    "exactly this shape:\n"
    "{\n"
    '  "recommendation": "approve|decline|investigate",\n'
    '  "confidence": 0,\n'
    '  "priority": "low|medium|high|critical",\n'
    '  "summary": "",\n'
    '  "reasoning": [],\n'
    '  "next_action": ""\n'
    "}\n"
    "confidence is an integer 0-100. reasoning is a list of short strings."
)


def build_user_prompt(context: dict) -> str:
    return (
        "Analyze the following flagged transaction context and return the JSON "
        "assessment.\n\n" + json.dumps(context, indent=2, default=str)
    )

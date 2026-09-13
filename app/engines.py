"""One call shape, two providers. The model name decides which.

Everything above this module — prompts, schema, weights, blockers, storage —
is provider-neutral. Only the wire call differs:

  claude-*        Anthropic Messages, adaptive thinking, effort, json_schema
  gpt-* / o*      OpenAI Responses, reasoning.effort, strict json_schema

Both return the same shape: text, a normalised stop reason, and a usage dict
keyed the way store.cost_usd expects.
"""
import os

ENGINES = ("claude", "openai")

# OpenAI models that reason before answering. Others (gpt-4.1, gpt-4o) reject
# the reasoning parameter outright. Override with PV_OPENAI_REASONING=on|off
# for a model this list does not know yet.
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Effort names match across providers (low..max). Older OpenAI models stop
# at high; the call downgrades once if the API says so.


def engine_for(model: str) -> str:
    return "claude" if model.startswith("claude-") else "openai"


def short_name(model: str) -> str:
    """What the UI shows: 'opus-5', 'gpt-5'."""
    return model.replace("claude-", "")


def complete(*, model: str, system: list[str], user: str, effort: str,
             max_tokens: int, schema: dict | None = None,
             schema_name: str = "review") -> dict:
    if engine_for(model) == "claude":
        return _anthropic(model, system, user, effort, max_tokens, schema)
    return _openai(model, system, user, effort, max_tokens, schema, schema_name)


# ------------------------------------------------------------------ Anthropic

def _anthropic(model, system, user, effort, max_tokens, schema):
    import anthropic

    client = anthropic.Anthropic()
    blocks = [{"type": "text", "text": s, "cache_control": {"type": "ephemeral"}}
              for s in system]
    output_config: dict = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}

    # Streaming: 32k output with thinking runs minutes, past the HTTP timeout.
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=blocks,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config=output_config,
    ) as stream:
        resp = stream.get_final_message()

    u = resp.usage
    return {
        "text": "".join(b.text for b in resp.content if b.type == "text"),
        "stop": resp.stop_reason,
        "usage": {
            "model": model, "effort": effort,
            "in": u.input_tokens, "out": u.output_tokens,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        },
    }


# --------------------------------------------------------------------- OpenAI

def _openai_reasons(model: str) -> bool:
    flag = os.environ.get("PV_OPENAI_REASONING", "auto").lower()
    if flag in ("on", "off"):
        return flag == "on"
    return model.startswith(_OPENAI_REASONING_PREFIXES)


def _openai(model, system, user, effort, max_tokens, schema, schema_name):
    from openai import BadRequestError, OpenAI

    # A 2,500-word review with reasoning can run several minutes; the SDK's
    # default 10-minute timeout is close enough to matter.
    client = OpenAI(timeout=1200, max_retries=2)

    kwargs: dict = {
        "model": model,
        "instructions": "\n\n".join(system),
        "input": user,
        "max_output_tokens": max_tokens,
    }
    if schema:
        kwargs["text"] = {"format": {
            "type": "json_schema", "name": schema_name,
            "schema": schema, "strict": True,
        }}
    if _openai_reasons(model):
        kwargs["reasoning"] = {"effort": effort}

    try:
        resp = client.responses.create(**kwargs)
    except BadRequestError as e:
        msg = str(e).lower()
        if "reasoning" not in kwargs:
            raise
        if "effort" in msg and effort not in ("low", "medium", "high"):
            # An older model that stops at high.
            kwargs["reasoning"] = {"effort": "high"}
        elif "reasoning" in msg:
            # A model the prefix list mis-classified: no reasoning at all.
            del kwargs["reasoning"]
        else:
            raise
        resp = client.responses.create(**kwargs)

    text = resp.output_text or ""
    stop = "end_turn"
    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", "") or "incomplete"
        stop = "max_tokens" if reason == "max_output_tokens" else reason
    elif resp.status != "completed":
        stop = resp.status
    if not text:
        for item in resp.output or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", "") == "refusal":
                    stop = "refusal"
                    text = getattr(part, "refusal", "")

    u = resp.usage
    in_details = getattr(u, "input_tokens_details", None)
    cached = getattr(in_details, "cached_tokens", 0) or 0
    cache_write = getattr(in_details, "cache_write_tokens", 0) or 0
    reasoning = getattr(getattr(u, "output_tokens_details", None),
                        "reasoning_tokens", 0) or 0
    return {
        "text": text,
        "stop": stop,
        "usage": {
            "model": model, "effort": effort,
            # OpenAI counts cached tokens inside input_tokens; split them out so
            # the pricing table charges each at its own rate.
            "in": u.input_tokens - cached, "out": u.output_tokens,
            "cache_write": cache_write, "cache_read": cached,
            "reasoning": reasoning,
        },
    }

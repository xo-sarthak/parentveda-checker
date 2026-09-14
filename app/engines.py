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


def openai_request(model, system, user, effort, max_tokens, schema=None,
                   schema_name="review") -> dict:
    """The Responses API body. Shared by the live call and the Batch API,
    so a batched review is byte-for-byte the same request as an instant one."""
    body: dict = {
        "model": model,
        "instructions": "\n\n".join(system),
        "input": user,
        "max_output_tokens": max_tokens,
    }
    if schema:
        body["text"] = {"format": {
            "type": "json_schema", "name": schema_name,
            "schema": schema, "strict": True,
        }}
    if _openai_reasons(model):
        body["reasoning"] = {"effort": effort}
    return body


def parse_openai(resp: dict, model: str, effort: str) -> dict:
    """A Responses object as a plain dict — from the SDK's model_dump() or a
    Batch output line — into the engine's common shape."""
    text = resp.get("output_text") or ""
    if not text:
        text = "".join(
            part.get("text", "")
            for item in resp.get("output") or []
            for part in item.get("content") or []
            if part.get("type") == "output_text")
    stop = "end_turn"
    status = resp.get("status")
    if status == "incomplete":
        reason = (resp.get("incomplete_details") or {}).get("reason") or "incomplete"
        stop = "max_tokens" if reason == "max_output_tokens" else reason
    elif status not in ("completed", None):
        stop = status
    if not text:
        for item in resp.get("output") or []:
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    stop = "refusal"
                    text = part.get("refusal", "")

    u = resp.get("usage") or {}
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
    cache_write = (u.get("input_tokens_details") or {}).get("cache_write_tokens") or 0
    reasoning = (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
    return {
        "text": text,
        "stop": stop,
        "usage": {
            "model": model, "effort": effort,
            # OpenAI counts cached tokens inside input_tokens; split them out so
            # the pricing table charges each at its own rate.
            "in": (u.get("input_tokens") or 0) - cached, "out": u.get("output_tokens") or 0,
            "cache_write": cache_write, "cache_read": cached,
            "reasoning": reasoning,
        },
    }


def _openai(model, system, user, effort, max_tokens, schema, schema_name):
    from openai import BadRequestError, OpenAI

    # A 2,500-word review with reasoning can run several minutes; the SDK's
    # default 10-minute timeout is close enough to matter.
    client = OpenAI(timeout=1200, max_retries=2)
    kwargs = openai_request(model, system, user, effort, max_tokens, schema, schema_name)

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

    return parse_openai(resp.model_dump(), model, effort)

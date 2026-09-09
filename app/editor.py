"""The Editor: applies accepted findings. Sees nothing else, scores nothing."""
import difflib
import re

import anthropic

from app import config, content


def _brief(items: list[dict]) -> str:
    """The accepted changes, in the order a human would work through them."""
    rank = {"must": 0, "should": 1, "polish": 2}
    items = sorted(items, key=lambda f: (rank.get(f["tier"], 9), f.get("position", 0)))
    out = []
    for i, f in enumerate(items, 1):
        proposed = f.get("edited_text") or f["proposed"]
        block = [f"## Change {i} — {f['kind']} ({f['tier']})", f"**{f['summary']}**"]
        if f.get("quote"):
            block.append(f"Find this passage:\n> {f['quote']}")
        block.append(f"Apply:\n{proposed}")
        if f.get("rationale"):
            block.append(f"_Why: {f['rationale']}_")
        out.append("\n\n".join(block))
    return "\n\n---\n\n".join(out)


def rewrite(article: str, accepted: list[dict], model: str | None = None,
            effort: str = "high") -> dict:
    if not accepted:
        raise ValueError("Nothing was accepted — there is nothing to apply.")

    client = anthropic.Anthropic()
    model = model or config.MODELS["edit"]

    system = [
        {"type": "text",
         "text": content.get("ruleset"),
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": content.get("editor"),
         "cache_control": {"type": "ephemeral"}},
    ]

    user = (
        f"# ACCEPTED CHANGES — apply all {len(accepted)}, and nothing else\n\n"
        f"{_brief(accepted)}\n\n---\n\n"
        f"# ARTICLE TO REVISE\n\n{article}"
    )

    with client.messages.stream(
        model=model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
    ) as stream:
        resp = stream.get_final_message()

    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"incomplete rewrite: stop_reason={resp.stop_reason}")

    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    body = re.sub(r"^```(?:markdown|md)?\n|\n```$", "", body).strip()

    return {
        "body": body,
        "diff": diff_summary(article, body),
        "_usage": {
            "model": model, "effort": effort,
            "in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
            "cache_write": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        },
    }


def _paras(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def diff_summary(before: str, after: str) -> dict:
    """Paragraph-level diff — what a reader would call 'the changes'."""
    a, b = _paras(before), _paras(after)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    changes, added, removed, edited = [], 0, 0, 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            added += j2 - j1
            changes += [{"kind": "added", "before": None, "after": p} for p in b[j1:j2]]
        elif tag == "delete":
            removed += i2 - i1
            changes += [{"kind": "removed", "before": p, "after": None} for p in a[i1:i2]]
        else:
            edited += max(i2 - i1, j2 - j1)
            for k in range(max(i2 - i1, j2 - j1)):
                changes.append({
                    "kind": "edited",
                    "before": a[i1 + k] if i1 + k < i2 else None,
                    "after": b[j1 + k] if j1 + k < j2 else None,
                })
    return {
        "added": added, "removed": removed, "edited": edited,
        "words_before": len(before.split()), "words_after": len(after.split()),
        "changes": changes,
    }

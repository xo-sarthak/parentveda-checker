"""The Editor: applies accepted findings. Sees nothing else, scores nothing."""
import difflib
import re

from app import config, content, engines


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


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         " ": " "})


# A line finding's `proposed` is meant to be the replacement text itself. Some
# come back as an instruction instead — "Shorten the Key Takeaway to: …",
# "Delete the sentence." Pasting that into the article is worse than paying
# the model to carry it out, so anything that reads like an instruction is
# routed to the rewrite instead of swapped in. Erring towards the model costs
# half a rupee; erring the other way puts editor-speak in a parent's article.
_IMPERATIVE = re.compile(
    r"^(?:if\b[^.\n]{0,90},\s*)?"
    r"(?:shorten|delete|remove|replace|add|move|rebuild|rewrite|tighten|cut|"
    r"change|merge|trim|reword|drop|split|insert|rename|retitle|reorder|convert|"
    r"expand|clarify|soften|strengthen|swap|restore|revise|open by|"
    r"state (?:explicitly|clearly|which|that)|keep (?:this|the|that)|"
    r"make (?:this|the|that|it)|turn (?:this|the|that|it)|use (?:this|the|that))\b",
    re.I)
_EDIT_NOUN = re.compile(
    r"\b(?:sentence|paragraph|section|sub-?section|takeaway|link|table|heading|"
    r"subhead|reference|block|line|article|intro|opening|closing|faq|bullet|list|"
    r"phrase|wording|claim|passage)\b", re.I)
_QUOTED = re.compile(r"[\"\u201c][^\"\u201d]{40,}[\"\u201d]")


def _is_instruction(text: str) -> bool:
    head = text.strip()[:160]
    if not _IMPERATIVE.match(head):
        return False
    return bool(_EDIT_NOUN.search(head) or _QUOTED.search(text)
                or re.search(r":\s*[\"\u201c]", head))


def applies_free(f: dict) -> bool:
    """Whether Apply can swap this finding in without a model — what the
    screen prices it at. Mirrors the test in apply(), minus the article
    match, which can only be known at apply time."""
    return (f.get("kind") == "line" and bool(f.get("quote"))
            and not _is_instruction((f.get("edited_text") or f.get("proposed") or "")))


def _locate(article: str, quote: str) -> str | None:
    """The quote as it actually appears in the article, or None.

    Models straighten curly quotes and collapse spaces when they copy a
    passage. One normalised pass catches nearly all of that; anything odder
    goes to the model rather than being guessed at."""
    if article.count(quote) == 1:
        return quote
    norm_a = article.translate(_QUOTES)
    norm_q = quote.translate(_QUOTES).strip()
    if norm_a.count(norm_q) != 1:
        return None
    i = norm_a.index(norm_q)
    return article[i:i + len(norm_q)]


def apply(article: str, accepted: list[dict], model: str | None = None,
          effort: str = "high") -> dict:
    """Apply accepted findings. Line findings are exact substitutions — the
    judge already wrote the replacement — so they cost nothing. Only
    structural findings, and any line finding whose quote no longer matches,
    go to the model."""
    if not accepted:
        raise ValueError("Nothing was accepted — there is nothing to apply.")

    body, swapped, to_model = article, [], []
    for f in accepted:
        text = (f.get("edited_text") or f["proposed"]).strip()
        found = (f["kind"] == "line" and f.get("quote") and not _is_instruction(text)
                 and _locate(body, f["quote"]))
        if found:
            body = body.replace(found, text, 1)
            swapped.append(f["summary"])
        else:
            to_model.append(f)

    if to_model:
        out = rewrite(body, to_model, model=model, effort=effort)
        body, usage = out["body"], out["_usage"]
    else:
        usage = None

    return {
        "body": body,
        "diff": diff_summary(article, body),
        "swapped": swapped,
        "rewritten": [f["summary"] for f in to_model],
        "_usage": usage,
    }


def rewrite(article: str, accepted: list[dict], model: str | None = None,
            effort: str = "high") -> dict:
    if not accepted:
        raise ValueError("Nothing was accepted — there is nothing to apply.")

    model = model or config.MODELS["edit"]

    user = (
        f"# ACCEPTED CHANGES — apply all {len(accepted)}, and nothing else\n\n"
        f"{_brief(accepted)}\n\n---\n\n"
        f"# ARTICLE TO REVISE\n\n{article}"
    )

    resp = engines.complete(
        model=model,
        system=[content.get("ruleset"), content.get("editor")],
        user=user,
        effort=effort,
        max_tokens=32000,
    )
    if resp["stop"] != "end_turn":
        raise RuntimeError(f"incomplete rewrite: stop_reason={resp['stop']}")

    body = resp["text"].strip()
    body = re.sub(r"^```(?:markdown|md)?\n|\n```$", "", body).strip()

    return {
        "body": body,
        "diff": diff_summary(article, body),
        "_usage": resp["usage"],
    }


def _paras(text: str) -> list[str]:
    # One unit per non-empty line. A .docx import has one newline per
    # paragraph; a model's output has blank lines between paragraphs but not
    # between list items or table rows. Splitting on blank lines makes those
    # two shapes diff as "everything changed"; lines diff cleanly.
    return [p.strip() for p in text.splitlines() if p.strip()]


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

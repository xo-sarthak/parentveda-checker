"""The Judge: reads an article, returns scores + feedback. Never edits."""
import json
from typing import Any

from app import config, content, engines

PARAMS = list(config.WEIGHTS.keys())

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "article_type", "article_level", "scores", "feedback",
        "verdict", "blockers", "expert_review",
    ],
    "properties": {
        "article_type": {
            "type": "string",
            "description": "educational | symptom | complication | pregnancy_loss | scan_test | nutrition | buying_guide | week_by_week | parenting_psychology | research_summary | milestones | india_specific | other",
        },
        "article_level": {
            "type": "object",
            "additionalProperties": False,
            "required": ["job", "scope", "length_note", "leave_alone",
                         "title_delivers", "title_note", "overlap_article",
                         "overlap_percent", "should_standalone",
                         "emotional_start", "opening_meets_it"],
            "properties": {
                "job": {
                    "type": "string",
                    "description": "The article's job in the reader's own words, as a question she is asking",
                },
                "scope": {
                    "type": "string",
                    "description": "One line: what kind of work this draft needs",
                },
                "length_note": {
                    "type": "string",
                    "description": "Word count and whether it suits this topic and type",
                },
                "leave_alone": {
                    "type": "string",
                    "description": "One sentence: what is working and must survive the edit",
                },
                "title_delivers": {"type": "boolean"},
                "title_note": {"type": "string"},
                "overlap_article": {
                    "type": ["string", "null"],
                    "description": "Title of the published article this overlaps, or null",
                },
                "overlap_percent": {"type": ["integer", "null"]},
                "should_standalone": {"type": "boolean"},
                "emotional_start": {
                    "type": "string",
                    "description": "The parent's emotional state on arrival, in a few words",
                },
                "opening_meets_it": {"type": "boolean"},
            },
        },
        # One array rather than twelve named objects. Twelve nested schemas
        # compiled into a grammar Anthropic rejected as too large.
        "scores": {
            "type": "array",
            "description": "All twelve parameters, one entry each",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["parameter", "score", "justification"],
                "properties": {
                    "parameter": {"type": "string", "enum": PARAMS},
                    "score": {"type": "number", "description": "0-10, one decimal"},
                    "justification": {
                        "type": "string",
                        "description": "Max 15 words, grounded in the text",
                    },
                },
            },
        },
        "feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tier", "type", "parameter", "summary", "quote",
                             "proposed", "rationale", "headline",
                             "needs_validation"],
                "properties": {
                    "tier": {"type": "string", "enum": ["must", "should", "polish"]},
                    "type": {"type": "string", "enum": ["line", "structural"]},
                    "parameter": {"type": "string", "enum": PARAMS},
                    "summary": {"type": "string", "description": "Under 12 words"},
                    "quote": {
                        "type": ["string", "null"],
                        "description": "Verbatim from the article. Required for line items.",
                    },
                    "proposed": {"type": "string"},
                    "rationale": {"type": "string", "description": "One sentence"},
                    "headline": {
                        "type": "boolean",
                        "description": "True on exactly one item: the single biggest issue",
                    },
                    "needs_validation": {
                        "type": "boolean",
                        "description": "True when a clinician should confirm rather than you asserting an error",
                    },
                },
            },
        },
        "verdict": {
            "type": "string",
            "enum": ["publish", "targeted_edits", "restructure",
                     "merge_into_existing", "not_ready"],
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
        "expert_review": {
            "type": "object",
            "additionalProperties": False,
            "required": ["required", "specialty", "reason"],
            "properties": {
                "required": {"type": "boolean"},
                "specialty": {"type": ["string", "null"]},
                "reason": {"type": "string"},
            },
        },
    },
}


def overall(scores: dict) -> float:
    """Weighted overall — computed here, never by the model."""
    return round(sum(scores[p]["score"] * w for p, w in config.WEIGHTS.items()), 2)


_FLOOR_WORDS = ("below the 8.5", "below 8.5", "< 8.5", "falls below",
                "fall below", "threshold")


def check_blockers(scores: dict, model_blockers: list[str]) -> list[str]:
    """Floor breaches are stated once, by us. The model's restatements are dropped."""
    out = [
        b for b in model_blockers
        if not any(w in b.lower() for w in _FLOOR_WORDS)
    ]
    for p in config.BLOCKED_PARAMS:
        if scores[p]["score"] < config.BLOCKER_FLOOR:
            out.append(
                f"{config.PARAMETER_LABELS[p]} scored {scores[p]['score']} — "
                f"below the {config.BLOCKER_FLOOR} floor"
            )
    return out


MAX_TOKENS = 32000


def parts(article: str, catalogue: str = "") -> tuple[list[str], str]:
    """System blocks and user message — the same for an instant review and a
    batched one, so scores stay comparable."""
    user = ""
    if catalogue:
        user += f"# PUBLISHED ARTICLE CATALOGUE\n\n{catalogue}\n\n---\n\n"
    user += f"# DRAFT ARTICLE UNDER REVIEW\n\n{article}"
    return [content.get("ruleset"), content.get("judge")], user


def finish(resp: dict) -> dict:
    """Engine response → review dict: scores normalised, overall and blockers
    computed here, usage attached."""
    if resp["stop"] != "end_turn":
        raise RuntimeError(
            f"incomplete response: stop_reason={resp['stop']}, "
            f"out={resp['usage']['out']} tokens"
        )
    data = json.loads(resp["text"])

    if isinstance(data.get("scores"), list):
        data["scores"] = {
            row["parameter"]: {"score": row["score"],
                               "justification": row.get("justification", "")}
            for row in data["scores"]
        }
    missing = [p for p in PARAMS if p not in data["scores"]]
    if missing:
        raise RuntimeError("model omitted parameters: " + ", ".join(missing))

    data["overall"] = overall(data["scores"])
    data["blockers"] = check_blockers(data["scores"], data.get("blockers", []))
    data["_usage"] = resp["usage"]
    return data


def review(article: str, catalogue: str = "", model: str | None = None,
           effort: str = "high") -> dict:
    model = model or config.MODELS["score"]
    system, user = parts(article, catalogue)
    resp = engines.complete(model=model, system=system, user=user, effort=effort,
                            max_tokens=MAX_TOKENS, schema=SCHEMA)
    return finish(resp)

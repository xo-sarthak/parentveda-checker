"""The Judge: reads an article, returns scores + feedback. Never edits."""
import json
from typing import Any

from app import config, content, engines

PARAMS = list(config.WEIGHTS.keys())
CARD = config.SCORECARD_KEYS

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "article_type", "article_level", "scores", "scorecard", "feedback",
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
        # Unweighted diagnostic detail. Does not affect the overall; it shows
        # a writer where the twelve weighted scores came from.
        "scorecard": {
            "type": "array",
            "description": "All twenty-seven review areas, one entry each",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["area", "score", "assessment"],
                "properties": {
                    "area": {"type": "string", "enum": CARD},
                    "score": {"type": "number", "description": "0-10, one decimal"},
                    "assessment": {
                        "type": "string",
                        "description": "Max 10 words, grounded in the text",
                    },
                },
            },
        },
        "feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tier", "type", "area", "scope", "parameter", "summary",
                             "quote", "proposed", "rationale", "headline",
                             "needs_validation"],
                "properties": {
                    "tier": {"type": "string", "enum": ["must", "should", "polish"]},
                    "type": {"type": "string", "enum": ["line", "structural", "image"],
                             "description": "image = a finding about a planned image prompt"},
                    # A plain string, not an enum: a second 27-value enum would
                    # push the compiled grammar past Anthropic's limit. Checked
                    # in code instead.
                    "area": {"type": "string",
                             "description": "The scorecard area key this finding is about"},
                    "scope": {"type": "string", "enum": ["passage", "section", "pervasive"],
                              "description": "pervasive = the problem runs through the article"},
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


def parts(article: str, catalogue: str = "", visuals: str = "") -> tuple[list[str], str]:
    """System blocks and user message — the same for an instant review and a
    batched one, so scores stay comparable."""
    user = ""
    if catalogue:
        user += f"# PUBLISHED ARTICLE CATALOGUE\n\n{catalogue}\n\n---\n\n"
    user += f"# DRAFT ARTICLE UNDER REVIEW\n\n{article}\n\n---\n\n"
    if visuals:
        user += ("# PLANNED VISUALS — image prompts and visual plan for this article\n\n"
                 "Not article text. Review every prompt here (ruleset §1C): findings on "
                 f"them use type \"image\".\n\n{visuals}")
    else:
        user += ("# PLANNED VISUALS\n\nNone supplied with this draft. Judge visuals on "
                 "the tables and diagrams in the text, and say in the visual assessments "
                 "that no image prompts were provided.")
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

    # The scorecard is diagnostic: a gap in it is worth noting, never fatal.
    card = {row["area"]: {"score": row["score"], "assessment": row.get("assessment", "")}
            for row in data.get("scorecard") or []}
    data["scorecard"] = {k: card.get(k) for k in CARD if card.get(k)}

    bind_scores_to_findings(data)
    data["overall"] = overall(data["scores"])
    data["blockers"] = check_blockers(data["scores"], data.get("blockers", []))
    data["_usage"] = resp["usage"]
    return data


def _kind(f: dict) -> str:
    if f.get("scope") == "pervasive":
        return "pervasive"
    if f["tier"] == "must":
        return "must_validate" if f.get("needs_validation") else "must"
    return f["tier"]


def bind_scores_to_findings(data: dict) -> None:
    """Make the numbers and the findings tell the same story.

    Every finding names a scorecard area; its weighted parameter is derived
    from that, never chosen separately. A finding then caps the score of the
    area it names — a must-fix area cannot read 9.0. Areas scored low with no
    finding behind them are recorded, so the screen can say so rather than
    hide it. The model's own number is kept wherever it was already stricter.
    """
    card = data["scorecard"]
    worst: dict[str, float] = {}
    pworst: dict[str, float] = {}
    for f in data.get("feedback") or []:
        area = (f.get("area") or "").strip()
        if area not in config.AREA_TO_PARAM:
            f["area"] = None
            continue
        f["parameter"] = config.AREA_TO_PARAM[area]
        k = _kind(f)
        worst[area] = min(worst.get(area, 10), config.CAPS_AREA[k])
        if k in config.CAPS_PARAM:
            p = f["parameter"]
            pworst[p] = min(pworst.get(p, 10), config.CAPS_PARAM[k])

    for area, cap in worst.items():
        row = card.get(area)
        if row and row["score"] > cap:
            row["capped_from"] = row["score"]
            row["score"] = cap
    for p, cap in pworst.items():
        row = data["scores"][p]
        if row["score"] > cap:
            row["capped_from"] = row["score"]
            row["score"] = cap

    data["unexplained"] = [a for a, row in card.items()
                           if row["score"] < config.UNEXPLAINED_BELOW and a not in worst]


def review(article: str, catalogue: str = "", model: str | None = None,
           effort: str = "high", visuals: str = "") -> dict:
    model = model or config.MODELS["score"]
    system, user = parts(article, catalogue, visuals)
    resp = engines.complete(model=model, system=system, user=user, effort=effort,
                            max_tokens=MAX_TOKENS, schema=SCHEMA)
    return finish(resp)

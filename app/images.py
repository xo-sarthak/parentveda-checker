"""Image briefs: a cover and up to two in-article visuals, as prompts."""
import json

from app import config, content, engines

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cover", "visuals", "note"],
    "properties": {
        "cover": {"$ref": "#/$defs/brief"},
        "visuals": {
            "type": "array",
            "maxItems": 2,
            "items": {"$ref": "#/$defs/brief"},
        },
        "note": {
            "type": "string",
            "description": "One line for the editor: why this many visuals, or what to watch for",
        },
    },
    "$defs": {
        "brief": {
            "type": "object",
            "additionalProperties": False,
            "required": ["placement", "purpose", "type", "prompt", "alt_text", "avoid"],
            "properties": {
                "placement": {"type": "string"},
                "purpose": {"type": "string"},
                "type": {"type": "string",
                         "enum": ["cover", "illustration", "diagram",
                                  "comparison", "timeline", "process"]},
                "prompt": {"type": "string"},
                "alt_text": {"type": "string"},
                "avoid": {"type": "string"},
            },
        }
    },
}


def briefs(article: str, title: str, model: str | None = None,
           effort: str | None = None) -> dict:
    model = model or config.MODELS["images"]
    effort = effort or "medium"
    resp = engines.complete(
        model=model,
        system=[content.get("images")],
        user=f"# ARTICLE\n\nTitle: {title}\n\n{article}",
        effort=effort,
        max_tokens=6000,
        schema=SCHEMA,
        schema_name="image_briefs",
    )
    if resp["stop"] != "end_turn":
        raise RuntimeError(f"incomplete image briefs: stop_reason={resp['stop']}")
    data = json.loads(resp["text"])
    data["_usage"] = resp["usage"]
    return data

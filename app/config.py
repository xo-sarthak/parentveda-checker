"""Configuration. Model choice is per-call so providers/tiers stay swappable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# Two engines, same prompts. Which one runs is a per-request choice from the
# UI; PV_ENGINE is only the default when the request does not say.
#
# Claude, settled in Phase 1: Opus scores, Sonnet applies. Only Opus caught
# the statutory errors in the maternity draft; rewriting to accepted
# instructions is the easier job and Sonnet does it well.
# OpenAI, tested 13 Sep 2026 on the cradle cap draft: gpt-5 matched Opus on
# findings at half the price; luna applied a five-item brief faithfully and
# wrote a complete doctor sheet for under a cent. See data/calibration/.
ENGINES = {
    "claude": {
        "score": os.environ.get("PV_MODEL_SCORE", "claude-opus-5"),
        "edit": os.environ.get("PV_MODEL_EDIT", "claude-sonnet-5"),
        "doctor": os.environ.get("PV_MODEL_DOCTOR", "claude-sonnet-5"),
        "images": os.environ.get("PV_MODEL_IMAGES", "claude-sonnet-5"),
    },
    "openai": {
        "score": os.environ.get("PV_OPENAI_MODEL_SCORE", "gpt-5"),
        "edit": os.environ.get("PV_OPENAI_MODEL_EDIT", "gpt-5.6-luna"),
        "doctor": os.environ.get("PV_OPENAI_MODEL_DOCTOR", "gpt-5.6-luna"),
        "images": os.environ.get("PV_OPENAI_MODEL_IMAGES", "gpt-5.6-luna"),
    },
}
ENGINE = os.environ.get("PV_ENGINE", "claude")
if ENGINE not in ENGINES:
    ENGINE = "claude"
MODELS = ENGINES[ENGINE]
EFFORT = os.environ.get("PV_EFFORT", "high")


def models(engine: str | None) -> dict:
    """The three models for an engine; the default engine when none is named."""
    return ENGINES.get(engine or ENGINE, ENGINES[ENGINE])

RULESET = ROOT / "corpus" / "compiled" / "parentveda-ruleset.md"
PROMPTS = ROOT / "app" / "prompts"

# Weights must match the ruleset. Overall is computed here, never by the model.
WEIGHTS = {
    "medical_accuracy": 0.15,
    "safety_framing": 0.15,
    "completeness": 0.10,
    "parent_usefulness": 0.10,
    "reader_journey": 0.10,
    "clarity": 0.10,
    "engagement": 0.075,
    "pattern_breaks": 0.05,
    "redundancy_focus": 0.05,
    "seo_intent": 0.075,
    "visual_strategy": 0.025,
    "publication_readiness": 0.025,
}

PARAMETER_LABELS = {
    "medical_accuracy": "Medical / Factual Accuracy",
    "safety_framing": "Safety & Risk Framing",
    "completeness": "Completeness / Coverage",
    "parent_usefulness": "Parent Usefulness / Actionability",
    "reader_journey": "Reader Journey & Structure",
    "clarity": "Clarity & Accessibility",
    "engagement": "Engagement & Emotional Resonance",
    "pattern_breaks": "Pattern Breaks & Readability",
    "redundancy_focus": "Redundancy & Focus",
    "seo_intent": "SEO & Search Intent",
    "visual_strategy": "Visual / Illustration Strategy",
    "publication_readiness": "Editorial / Publication Readiness",
}

BLOCKER_FLOOR = 8.5
BLOCKED_PARAMS = ("medical_accuracy", "safety_framing")

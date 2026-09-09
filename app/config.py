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

# Settled in Phase 1: Opus scores, Sonnet applies. Only Opus caught the
# statutory errors in the maternity draft; rewriting to accepted instructions
# is the easier job and Sonnet does it well.
MODELS = {
    "score": os.environ.get("PV_MODEL_SCORE", "claude-opus-5"),
    "edit": os.environ.get("PV_MODEL_EDIT", "claude-sonnet-5"),
    "doctor": os.environ.get("PV_MODEL_DOCTOR", "claude-sonnet-5"),
}
EFFORT = os.environ.get("PV_EFFORT", "high")

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

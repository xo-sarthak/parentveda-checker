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

# The detailed scorecard: 27 unweighted areas, scored alongside the twelve.
# It does not touch the overall or the blockers — it exists so a writer can
# see *where* a score came from at a finer grain than twelve rows allow.
# Order is the order it is shown in, grouped by theme rather than by number.
SCORECARD = [
    ("medical_accuracy_detail", "Medical accuracy", "Definitions, causes, symptoms, timelines, prevalence, management and claims"),
    ("evidence_validation", "Evidence & validation", "Important claims supported by credible evidence; what needs verifying"),
    ("medical_safety", "Medical safety", "No diagnosis, individual interpretation, unsafe treatment or medication advice, false reassurance, dangerous omissions"),
    ("red_flags", "Safety / red flags", "Warning signs, escalation points and when-to-see-a-doctor, correctly framed"),
    ("clinical_nuance", "Clinical nuance", "Uncertainty, exceptions, overlap and context rather than simplistic claims"),
    ("trust_credibility", "Trust & credibility", "Sounds responsible and medically trustworthy, not overconfident or sensational"),
    ("parent_comprehension", "Parent comprehension", "Understandable without medical knowledge; jargon, ambiguity, unexplained terms"),
    ("parent_practicality", "Parent usefulness", "What the information means and what she can reasonably do next"),
    ("actionability", "Actionability", "What to observe, what to do, what not to do, when to seek help"),
    ("answer_first", "Answer-first clarity", "The main question answered early, in the opening or quick answer"),
    ("structure_ia", "Structure & architecture", "Logical sequence, each section with a purpose, a coherent reading journey"),
    ("question_coverage", "Question coverage", "The natural questions a parent has; FAQs useful rather than filler"),
    ("content_completeness", "Content completeness", "Important aspects of the topic present for this audience"),
    ("engagement_quality", "Engagement", "Interesting and emotionally relevant without clickbait, fear or drama"),
    ("central_idea", "Central idea", "Is there one strong central idea holding the article together"),
    ("insight_vs_textbook", "Insight vs textbook", "Genuinely insightful with depth, or a generic textbook read"),
    ("tone_voice", "Tone & brand voice", "Calm, warm, trustworthy, supportive; never patronising, cute or alarmist"),
    ("conciseness", "Conciseness / density", "Every section earning its space; unnecessary detail or long passages"),
    ("repetition", "Repetition", "Ideas duplicated across intro, takeaways, sections, Insight, FAQs, conclusion"),
    ("editorial_quality", "Editorial quality", "Grammar, sentences, headings, tables, transitions, polish"),
    ("seo_search_intent", "SEO / search intent", "Answers what parents search; headings search-friendly without stuffing"),
    ("visual_opportunity", "Visual opportunity", "Where an image, comparison, diagram or table would materially help"),
    ("visual_necessity", "Visual necessity", "Educational visuals distinguished from decorative ones"),
    ("audience_relevance", "Audience & stage relevance", "Right for the baby's or pregnancy stage and the ParentVeda audience"),
    ("indian_relevance", "Indian relevance", "Indian context reflected without stereotyping or unsupported claims"),
    ("cross_consistency", "Cross-content consistency", "Terminology, claims and recommendations consistent with related content"),
    ("internal_linking", "Internal linking / journey", "Related articles and next steps genuinely useful and connected"),
]
SCORECARD_KEYS = [k for k, _, _ in SCORECARD]
SCORECARD_LABELS = {k: label for k, label, _ in SCORECARD}

# Which weighted parameter each area rolls up into. A finding names its area;
# its parameter is derived from this, so the two can never disagree.
AREA_TO_PARAM = {
    'medical_accuracy_detail': 'medical_accuracy',
    'evidence_validation': 'medical_accuracy',
    'medical_safety': 'safety_framing',
    'red_flags': 'safety_framing',
    'clinical_nuance': 'medical_accuracy',
    'trust_credibility': 'safety_framing',
    'parent_comprehension': 'clarity',
    'parent_practicality': 'parent_usefulness',
    'actionability': 'parent_usefulness',
    'answer_first': 'reader_journey',
    'structure_ia': 'reader_journey',
    'question_coverage': 'completeness',
    'content_completeness': 'completeness',
    'engagement_quality': 'engagement',
    'central_idea': 'reader_journey',
    'insight_vs_textbook': 'engagement',
    'tone_voice': 'engagement',
    'conciseness': 'redundancy_focus',
    'repetition': 'redundancy_focus',
    'editorial_quality': 'publication_readiness',
    'seo_search_intent': 'seo_intent',
    'visual_opportunity': 'pattern_breaks',
    'visual_necessity': 'visual_strategy',
    'audience_relevance': 'completeness',
    'indian_relevance': 'completeness',
    'cross_consistency': 'publication_readiness',
    'internal_linking': 'publication_readiness',
}

# Score ceilings a finding imposes on the area it names — and, for the two
# heaviest kinds, on the weighted parameter above it. Enforced in code, so a
# review cannot flag a real problem and still score the area as fine.
CAPS_AREA = {"pervasive": 6.9, "must": 7.9, "must_validate": 8.4,
             "should": 8.7, "polish": 8.9}
CAPS_PARAM = {"pervasive": 7.4, "must": 8.4}
UNEXPLAINED_BELOW = 8.5   # an area under this with no finding is flagged

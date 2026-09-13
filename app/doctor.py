"""The verification sheet: what a clinician needs to sign off without reading."""
from app import config, content, engines

# Which specialty an article type most often needs. The review's own
# expert_review.specialty wins when it names one; this is the fallback.
TYPE_TO_SPECIALTY = {
    "pregnancy_loss": "Obs-gynae",
    "complication": "Obs-gynae",
    "scan_test": "Obs-gynae",
    "week_by_week": "Obs-gynae",
    "nutrition": "Women's health",
    "milestones": "Child psychology",
    "parenting_psychology": "Child psychology",
    "symptom": "Women's health",
}

# Roster specialties, as seeded. Used to match a required specialty to people.
SPECIALTY_ALIASES = {
    "obstetrician": "Obs-gynae", "obstetrics": "Obs-gynae",
    "gynaecologist": "Obs-gynae", "obstetrician-gynaecologist": "Obs-gynae",
    "lactation consultant": "Lactation", "ibclc": "Lactation",
    "paediatrician": "Child health", "pediatrician": "Child health",
    "psychologist": "Child psychology", "child psychologist": "Child psychology",
    "physiotherapist": "Women's health", "physiotherapy": "Women's health",
}


def required_specialty(review: dict) -> str:
    named = (review.get("expert_review") or {}).get("specialty") or ""
    key = named.lower().strip()
    for alias, canonical in SPECIALTY_ALIASES.items():
        if alias in key:
            return canonical
    if named:
        return named
    return TYPE_TO_SPECIALTY.get(review.get("article_type", ""), "Women's health")


def sheet(article: str, title: str, review: dict,
          model: str | None = None, effort: str | None = None) -> dict:
    model = model or config.MODELS["doctor"]
    effort = effort or config.EFFORT

    resp = engines.complete(
        model=model,
        system=[content.get("ruleset"), content.get("doctor")],
        user=f"# ARTICLE: {title}\n\n{article}",
        effort=effort,
        max_tokens=16000,
    )

    body = resp["text"].strip()
    return {
        "specialty": required_specialty(review),
        "body": body,
        "_usage": resp["usage"],
    }

"""Scenario skill classification taxonomy and verified scenario catalog for Kovaak Agent.

Supports multi-category skill tagging per scenario, deterministic pattern-based
classification for unfamiliar scenarios, manual player tag overrides persisted in
SQLite, and a verified catalog of well-known KovaaK's training scenarios (with no
fabricated playlist share codes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


SKILL_CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "id": "static_clicking",
        "label": "Static Clicking",
        "short_label": "Static",
        "description": "Precision acquisition and micro-adjustment on stationary targets.",
    },
    {
        "id": "dynamic_clicking",
        "label": "Dynamic Clicking",
        "short_label": "Dynamic",
        "description": "Timing-based clicks on moving, bouncing, or strafing targets.",
    },
    {
        "id": "precise_switching",
        "label": "Precise Target Switching",
        "short_label": "Precise TS",
        "description": "Controlled multi-target transitions requiring sustained finish on small targets.",
    },
    {
        "id": "speed_switching",
        "label": "Speed Target Switching",
        "short_label": "Speed TS",
        "description": "Rapid wide-angle flick transitions and quick target confirmation.",
    },
    {
        "id": "smooth_tracking",
        "label": "Smooth Tracking",
        "short_label": "Smoothness",
        "description": "Steady velocity matching and tension control on predictable trajectories.",
    },
    {
        "id": "reactive_tracking",
        "label": "Reactive Tracking",
        "short_label": "Reactive",
        "description": "Fast direction-change reading on abrupt or erratic strafes.",
    },
    {
        "id": "evasive_tracking",
        "label": "Evasive Tracking",
        "short_label": "Evasive",
        "description": "Sustained tracking against multi-directional vertical and horizontal dodges.",
    },
    {
        "id": "movement_reading",
        "label": "Movement & Target Reading",
        "short_label": "Reading",
        "description": "Trajectory prediction, dodge-assisted aim, and reading target reversal cues.",
    },
    {
        "id": "benchmark",
        "label": "Benchmark Scenarios",
        "short_label": "Benchmark",
        "description": "Standardized community benchmark scenarios used to evaluate baseline progress.",
    },
)

SKILL_CATEGORY_MAP: dict[str, dict[str, str]] = {item["id"]: item for item in SKILL_CATEGORIES}
SKILL_CATEGORY_IDS: tuple[str, ...] = tuple(item["id"] for item in SKILL_CATEGORIES)


@dataclass(frozen=True)
class CatalogScenario:
    name: str
    categories: tuple[str, ...]
    difficulty_tier: int
    default_duration_seconds: int
    cs2_relevant: bool
    easier_variant: str | None
    harder_variant: str | None
    technique_focus: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "categories": list(self.categories),
            "difficulty_tier": self.difficulty_tier,
            "default_duration_seconds": self.default_duration_seconds,
            "cs2_relevant": self.cs2_relevant,
            "easier_variant": self.easier_variant,
            "harder_variant": self.harder_variant,
            "technique_focus": self.technique_focus,
        }


VERIFIED_SCENARIO_CATALOG: tuple[CatalogScenario, ...] = (
    # Static Clicking
    CatalogScenario(
        "1wall6targets TE",
        ("static_clicking",),
        1,
        60,
        True,
        None,
        "1wall6targets small",
        "Use a clean two-part motion: decisive initial flick followed by deliberate micro-confirmation before clicking.",
    ),
    CatalogScenario(
        "1wall6targets small",
        ("static_clicking",),
        2,
        60,
        True,
        "1wall6targets TE",
        "1wall6targets extra small",
        "Prioritize clean hit confirmation on small targets before attempting higher click cadence.",
    ),
    CatalogScenario(
        "1wall6targets extra small",
        ("static_clicking",),
        3,
        60,
        True,
        "1wall6targets small",
        None,
        "Relax grip tension during micro-corrections so small adjustments stay controlled.",
    ),
    CatalogScenario(
        "ww3t Voltaic",
        ("static_clicking", "benchmark"),
        2,
        60,
        True,
        "1wall6targets TE",
        "1w4ts Voltaic",
        "Keep paths straight across wide angles and settle briefly on each target before firing.",
    ),
    CatalogScenario(
        "ww5t Voltaic",
        ("static_clicking",),
        2,
        60,
        True,
        "1wall6targets TE",
        "ww3t Voltaic",
        "Chain transitions cleanly between clustered targets without rushing missed follow-up clicks.",
    ),
    CatalogScenario(
        "1w4ts Voltaic",
        ("static_clicking", "benchmark"),
        3,
        60,
        True,
        "ww3t Voltaic",
        "1wall6targets extra small",
        "Balance transition pace with >88% accuracy rather than forcing raw clicks per minute.",
    ),
    CatalogScenario(
        "6 Sphere Hipfire Voltaic",
        ("static_clicking", "benchmark"),
        2,
        60,
        True,
        "1wall6targets TE",
        "1wall6targets small",
        "Treat each sphere as a crisp stop-and-confirm click rather than continuous dragging.",
    ),
    CatalogScenario(
        "VT Multiclick 120 Novice S4",
        ("static_clicking", "benchmark"),
        1,
        60,
        True,
        None,
        "ww3t Voltaic",
        "Maintain steady rhythm across targets and avoid double-clicking through uncertainty.",
    ),
    CatalogScenario(
        "pokeball Frenzy Auto Small",
        ("static_clicking", "speed_switching"),
        2,
        60,
        True,
        "1wall6targets small",
        None,
        "Hold fire and snap cleanly from target to target to build smooth deceleration without click-spam.",
    ),
    CatalogScenario(
        "Tile Frenzy 180",
        ("static_clicking", "speed_switching"),
        1,
        60,
        False,
        None,
        "pokeball Frenzy Auto Small",
        "Keep wider horizontal transitions controlled and maintain consistent pacing across the run.",
    ),
    CatalogScenario(
        "Microshot Speed",
        ("static_clicking",),
        2,
        60,
        True,
        "1wall6targets TE",
        "1wall6targets extra small",
        "Focus on quiet wrist/finger micro-adjustments around center screen.",
    ),
    CatalogScenario(
        "Reflex Flick",
        ("static_clicking",),
        2,
        60,
        True,
        "1wall6targets TE",
        "1wall6targets small",
        "Stay composed during short target windows and confirm visual lock before clicking.",
    ),

    # Dynamic Clicking
    CatalogScenario(
        "Pasu Voltaic Easy",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        1,
        60,
        True,
        None,
        "Pasu Voltaic",
        "Track the bot's arc briefly to read its direction before committing to the click.",
    ),
    CatalogScenario(
        "Pasu Voltaic",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        2,
        60,
        True,
        "Pasu Voltaic Easy",
        "Popcorn Voltaic",
        "Avoid chasing unpredictable reversals; wait a beat for stable linear travel before clicking.",
    ),
    CatalogScenario(
        "VT Pasu Novice S4",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        1,
        60,
        True,
        None,
        "Pasu Voltaic Easy",
        "Match target velocity momentarily right before clicking so shots land cleanly.",
    ),
    CatalogScenario(
        "Bounce 180",
        ("dynamic_clicking", "movement_reading"),
        2,
        60,
        True,
        "Pasu Voltaic Easy",
        "Popcorn Voltaic",
        "Anticipate apex slowdown on each bounce arc rather than clicking mid-acceleration.",
    ),
    CatalogScenario(
        "Popcorn Goated TI",
        ("dynamic_clicking", "movement_reading"),
        2,
        60,
        False,
        "Bounce 180",
        "Popcorn Voltaic",
        "Wait for the peak of vertical arcs where target vertical speed briefly approaches zero.",
    ),
    CatalogScenario(
        "Popcorn Voltaic",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        3,
        60,
        False,
        "Popcorn Goated TI",
        None,
        "Time clicks near arc apexes and reset smoothly to the next highest-probability target.",
    ),
    CatalogScenario(
        "VT Floatshot Novice S4",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        1,
        60,
        True,
        None,
        "Bounce 180",
        "Read vertical descent speed smoothly and click on predictable intercept lines.",
    ),
    CatalogScenario(
        "VT Frogtagon Novice S4",
        ("dynamic_clicking", "movement_reading", "benchmark"),
        1,
        60,
        True,
        None,
        "Pasu Voltaic Easy",
        "Keep crosshair near active lanes and time clicks on predictable mid-stride trajectories.",
    ),
    CatalogScenario(
        "Floating Heads Timing 400%",
        ("dynamic_clicking", "movement_reading"),
        2,
        60,
        True,
        "VT Floatshot Novice S4",
        "Pasu Voltaic",
        "Practice horizontal head-height tracking into single-tap timing confirmation.",
    ),

    # Precise & Speed Target Switching
    CatalogScenario(
        "voxTargetSwitch Click",
        ("speed_switching", "precise_switching"),
        2,
        60,
        True,
        "PatTargetSwitch",
        "psalmTargetSwitch",
        "Snap briskly between targets and maintain calm hold until the kill confirms before moving on.",
    ),
    CatalogScenario(
        "PatTargetSwitch",
        ("speed_switching",),
        1,
        60,
        True,
        None,
        "voxTargetSwitch Click",
        "Commit cleanly to each target and keep pathing tight between adjacent clusters.",
    ),
    CatalogScenario(
        "psalmTargetSwitch",
        ("precise_switching", "speed_switching"),
        3,
        60,
        True,
        "voxTargetSwitch Click",
        None,
        "Stay patient on small evasive targets; avoid flicking away before full target elimination.",
    ),
    CatalogScenario(
        "devTargetSwitch Goated",
        ("speed_switching",),
        2,
        60,
        True,
        "PatTargetSwitch",
        "psalmTargetSwitch",
        "Link transitions fluidly without pausing between kills.",
    ),
    CatalogScenario(
        "kinTargetSwitch",
        ("precise_switching", "movement_reading"),
        2,
        60,
        True,
        "PatTargetSwitch",
        "psalmTargetSwitch",
        "Match target drift smoothly on initial landing so TTK stays tight.",
    ),
    CatalogScenario(
        "B180T Voltaic",
        ("precise_switching", "dynamic_clicking", "benchmark"),
        2,
        60,
        False,
        "Bounce 180 Tracking",
        "psalmTargetSwitch",
        "Lead arcing targets slightly through apex transitions and hold smooth tracking until kill.",
    ),
    CatalogScenario(
        "Bounce 180 Tracking",
        ("precise_switching", "smooth_tracking"),
        1,
        60,
        False,
        None,
        "B180T Voltaic",
        "Finish each bouncing target before switching eyes to the next spawn.",
    ),
    CatalogScenario(
        "TamTargetSwitch Smooth",
        ("precise_switching", "smooth_tracking"),
        2,
        60,
        True,
        "Bounce 180 Tracking",
        "psalmTargetSwitch",
        "Keep wrist tension low after the initial switch so tracking stays level until kill.",
    ),
    CatalogScenario(
        "VT TamTargetSwitch Control Novice S4",
        ("precise_switching", "smooth_tracking", "benchmark"),
        1,
        60,
        True,
        None,
        "TamTargetSwitch Smooth",
        "Prioritize clean target acquisition and steady follow-through over frantic flicks.",
    ),
    CatalogScenario(
        "VT PatCircleSwitch Novice S4",
        ("speed_switching", "benchmark"),
        1,
        60,
        True,
        None,
        "voxTargetSwitch Click",
        "Build smooth circular transitions with decisive stops on each target.",
    ),

    # Smooth, Reactive & Evasive Tracking + Movement Reading
    CatalogScenario(
        "Smoothbot Voltaic Easy",
        ("smooth_tracking", "benchmark"),
        1,
        60,
        True,
        None,
        "Smoothbot Voltaic",
        "Match the bot's speed continuously with relaxed forearm/wrist motion rather than micro-flicking.",
    ),
    CatalogScenario(
        "Smoothbot Voltaic",
        ("smooth_tracking", "benchmark"),
        2,
        60,
        True,
        "Smoothbot Voltaic Easy",
        "Controlsphere",
        "Focus on keeping crosshair velocity glued to target center across long sweeps.",
    ),
    CatalogScenario(
        "VT Smoothbot Novice S4",
        ("smooth_tracking", "benchmark"),
        1,
        60,
        True,
        None,
        "Smoothbot Voltaic Easy",
        "Use steady continuous tracking and avoid tense wrist corrections.",
    ),
    CatalogScenario(
        "Controlsphere",
        ("smooth_tracking", "precise_switching"),
        3,
        60,
        True,
        "Smoothbot Voltaic",
        None,
        "Maintain light finger tension to track slow, small directional arcs cleanly.",
    ),
    CatalogScenario(
        "whisphere",
        ("smooth_tracking",),
        2,
        60,
        True,
        "Smoothbot Voltaic Easy",
        "Controlsphere",
        "Smooth out micro-jitters by focusing on steady center-of-target visual focus.",
    ),
    CatalogScenario(
        "Ascended Tracking v3",
        ("smooth_tracking",),
        1,
        60,
        True,
        None,
        "Smoothbot Voltaic Easy",
        "Keep steady tracking speed across horizontal and gentle vertical arcs.",
    ),
    CatalogScenario(
        "Close Long Strafes Invincible",
        ("smooth_tracking", "reactive_tracking"),
        1,
        120,
        True,
        None,
        "Close Fast Strafes Invincible",
        "Track smoothly across long strafes and react cleanly only when a real direction change occurs.",
    ),
    CatalogScenario(
        "Close Fast Strafes Invincible",
        ("reactive_tracking", "movement_reading"),
        2,
        60,
        True,
        "Close Long Strafes Invincible",
        "Ground Plaza Voltaic",
        "Avoid predicting fake strafes; react visually to confirmed direction changes.",
    ),
    CatalogScenario(
        "Midrange Long Strafes Invincible",
        ("smooth_tracking",),
        1,
        60,
        True,
        "Ascended Tracking v3",
        "Close Long Strafes Invincible",
        "Keep crosshair pace uniform across midrange horizontal travel.",
    ),
    CatalogScenario(
        "Cata IC Long Strafes",
        ("smooth_tracking",),
        1,
        60,
        True,
        None,
        "Close Long Strafes Invincible",
        "Relax grip and match target velocity smoothly end-to-end.",
    ),
    CatalogScenario(
        "FuglaaXYLongstrafes",
        ("smooth_tracking", "movement_reading"),
        1,
        60,
        True,
        None,
        "FuglaaXYShortstrafes",
        "Track full strafe legs evenly and reset calmly at reversal edges.",
    ),
    CatalogScenario(
        "FuglaaXYShortstrafes",
        ("reactive_tracking", "movement_reading"),
        2,
        60,
        True,
        "FuglaaXYLongstrafes",
        "Close Fast Strafes Invincible",
        "Read short strafe reversals visually without twitching ahead of the bot.",
    ),
    CatalogScenario(
        "FuglaaXYZ Voltaic",
        ("evasive_tracking", "reactive_tracking", "benchmark"),
        2,
        60,
        False,
        "FuglaaXYShortstrafes",
        "Air Angelic 4 Voltaic",
        "Blend smooth vertical adjustment with calm horizontal reversal reading.",
    ),
    CatalogScenario(
        "Ground Plaza Voltaic Easy",
        ("reactive_tracking", "evasive_tracking", "movement_reading", "benchmark"),
        1,
        60,
        True,
        None,
        "Ground Plaza Voltaic",
        "Adapt smoothly between long strafe phases and short reactive bot phases.",
    ),
    CatalogScenario(
        "Ground Plaza Voltaic",
        ("reactive_tracking", "evasive_tracking", "movement_reading", "benchmark"),
        2,
        60,
        True,
        "Ground Plaza Voltaic Easy",
        "Air Angelic 4 Voltaic",
        "Wait for confirmed directional cues on tight strafes before snapping back onto center.",
    ),
    CatalogScenario(
        "VT Plaza Novice S4",
        ("reactive_tracking", "evasive_tracking", "benchmark"),
        1,
        60,
        True,
        None,
        "Ground Plaza Voltaic Easy",
        "Stay centered on target silhouette and avoid over-reacting to micro-feints.",
    ),
    CatalogScenario(
        "Air Angelic 4 Voltaic",
        ("evasive_tracking", "reactive_tracking", "benchmark"),
        2,
        60,
        False,
        "VT Air Novice S4",
        "PGTI Voltaic",
        "Keep vertical mouse control fluid through high-angle aerial direction shifts.",
    ),
    CatalogScenario(
        "VT Air Novice S4",
        ("evasive_tracking", "reactive_tracking", "benchmark"),
        1,
        60,
        False,
        None,
        "Air Angelic 4 Voltaic",
        "Read each bot phase calmly and smoothly absorb vertical jumps.",
    ),
    CatalogScenario(
        "PGTI Voltaic Easy",
        ("evasive_tracking", "smooth_tracking", "movement_reading", "benchmark"),
        2,
        60,
        False,
        "Smoothbot Voltaic Easy",
        "PGTI Voltaic",
        "Mirror target elevation smoothly across curved trajectories.",
    ),
    CatalogScenario(
        "PGTI Voltaic",
        ("evasive_tracking", "smooth_tracking", "movement_reading", "benchmark"),
        3,
        60,
        False,
        "PGTI Voltaic Easy",
        None,
        "Maintain steady tracking rhythm without losing posture during steep vertical arcs.",
    ),
)

CATALOG_BY_NAME_LOWER: dict[str, CatalogScenario] = {
    entry.name.lower(): entry for entry in VERIFIED_SCENARIO_CATALOG
}


def classify_scenario(name: str) -> list[str]:
    """Deterministically assign one or more skill categories to a KovaaK's scenario name."""
    clean = (name or "").strip()
    if not clean:
        return ["unclassified"]

    exact = CATALOG_BY_NAME_LOWER.get(clean.lower())
    if exact:
        return list(exact.categories)

    low = clean.lower()
    tags: set[str] = set()

    # Benchmark marker check (independent so benchmark scenarios also keep mechanical tags).
    if re.search(r"\b(vt|voltaic|viscose|revosect|ra|benchmark|novice|intermediate|advanced|s4|s5)\b", low):
        tags.add("benchmark")

    # Static clicking
    if re.search(r"(1w\d*t|1wall\d*target|ww\d*t|6 sphere|tile frenzy|pokeball|multiclick|microshot|reflex flick|static|sixshot|widow)", low):
        tags.add("static_clicking")

    # Dynamic clicking
    if re.search(r"(pasu|popcorn|bounce\s*180(?!.*track)|b180(?!t)|floatshot|frogtagon|floating heads|dynamic|voxtargetswitch click|arc\s*\d+)", low):
        tags.add("dynamic_clicking")

    # Precise target switching
    if re.search(r"(precise.*switch|control.*switch|psalmtargetswitch|tamtargetswitch|kintargetswitch|controlsphere|b180t|bounce 180 tracking)", low):
        tags.add("precise_switching")

    # Speed target switching
    if re.search(r"(targetswitch|ts\b|pattargetswitch|voxtargetswitch|devtargetswitch|patcircleswitch|pokeball|tile frenzy)", low):
        tags.add("speed_switching")

    # Smooth tracking
    if re.search(r"(smoothbot|controlsphere|whisphere|ascended tracking|long strafes|smooth|thin aiming|centering|pgti)", low):
        tags.add("smooth_tracking")

    # Reactive tracking
    if re.search(r"(fast strafes|shortstrafes|reactive|plaza|strafe|close.*invincible|air\b|fuglaaxy)", low):
        tags.add("reactive_tracking")

    # Evasive tracking
    if re.search(r"(air\b|plaza|pgti|fuglaaxyz|evasive|dodge|aerial)", low):
        tags.add("evasive_tracking")

    # Movement & target reading
    if re.search(r"(pasu|popcorn|bounce|plaza|strafe|dodge|movement|reading|pgti|frogtagon|floatshot|lgc)", low):
        tags.add("movement_reading")

    # Generic fallback tokens if only benchmark or nothing matched yet
    mechanical = tags - {"benchmark"}
    if not mechanical:
        if "click" in low or "flick" in low or "shot" in low:
            tags.add("static_clicking")
        elif "switch" in low:
            tags.add("speed_switching")
        elif "track" in low:
            tags.add("smooth_tracking")

    ordered = [cat for cat in SKILL_CATEGORY_IDS if cat in tags]
    return ordered if ordered else ["unclassified"]


def normalize_variant_stem(name: str) -> str:
    """Reduce difficulty/version suffixes to detect sibling scenario variants."""
    text = (name or "").strip().lower()
    text = re.sub(r"\b(easy|hard|extra small|small|tiny|novice|intermediate|advanced|reload|invincible|thin|larger|slightly larger|s\d+|v\d+|\d+%)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class TaxonomyManager:
    """Manages multi-category scenario classifications and manual player overrides."""

    def __init__(self, database: Any):
        self.database = database

    @staticmethod
    def categories() -> list[dict[str, str]]:
        return [dict(item) for item in SKILL_CATEGORIES]

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        return [item.to_dict() for item in VERIFIED_SCENARIO_CATALOG]

    @staticmethod
    def catalog_entry(name: str) -> CatalogScenario | None:
        return CATALOG_BY_NAME_LOWER.get((name or "").strip().lower())

    def get_tags(self, scenario: str) -> dict[str, Any]:
        clean = (scenario or "").strip()
        if not clean:
            return {"scenario": clean, "categories": ["unclassified"], "source": "auto", "auto_categories": ["unclassified"]}
        auto_tags = classify_scenario(clean)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT category, source FROM scenario_tags WHERE scenario = ? ORDER BY rowid ASC",
                (clean,),
            ).fetchall()
        manual = [row["category"] for row in rows if row["source"] == "manual" and row["category"] in SKILL_CATEGORY_MAP]
        if manual:
            ordered = [cat for cat in SKILL_CATEGORY_IDS if cat in set(manual)]
            return {"scenario": clean, "categories": ordered, "source": "manual", "auto_categories": auto_tags}
        return {"scenario": clean, "categories": auto_tags, "source": "auto", "auto_categories": auto_tags}

    def set_manual_tags(self, scenario: str, categories: Iterable[str]) -> dict[str, Any]:
        clean = (scenario or "").strip()
        if not clean or len(clean) > 200:
            raise ValueError("Provide a scenario name (1-200 characters).")
        cleaned_cats = [str(cat).strip() for cat in categories if str(cat).strip()]
        invalid = [cat for cat in cleaned_cats if cat not in SKILL_CATEGORY_MAP]
        if invalid:
            raise ValueError(f"Unknown skill category: {', '.join(invalid)}")
        unique_ordered = [cat for cat in SKILL_CATEGORY_IDS if cat in set(cleaned_cats)]
        if not unique_ordered:
            raise ValueError("Select at least one skill category.")
        with self.database.connect() as connection:
            connection.execute("DELETE FROM scenario_tags WHERE scenario = ?", (clean,))
            for category in unique_ordered:
                connection.execute(
                    "INSERT INTO scenario_tags(scenario, category, source, confidence) VALUES(?, ?, 'manual', 1.0)",
                    (clean, category),
                )
        return self.get_tags(clean)

    def reset_tags(self, scenario: str) -> dict[str, Any]:
        clean = (scenario or "").strip()
        if not clean:
            raise ValueError("Provide a scenario name.")
        with self.database.connect() as connection:
            connection.execute("DELETE FROM scenario_tags WHERE scenario = ?", (clean,))
        return self.get_tags(clean)

    def all_scenario_tags(self, scenarios: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Batch-resolve tags for a collection of scenario names in a single SQL query."""
        names = sorted({str(s).strip() for s in scenarios if str(s or "").strip()})
        manual_map: dict[str, list[str]] = {}
        if names:
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT scenario, category FROM scenario_tags WHERE source = 'manual'"
                ).fetchall()
            for row in rows:
                if row["category"] in SKILL_CATEGORY_MAP:
                    manual_map.setdefault(row["scenario"], []).append(row["category"])

        result: dict[str, dict[str, Any]] = {}
        for name in names:
            auto_tags = classify_scenario(name)
            manual = manual_map.get(name)
            if manual:
                ordered = [cat for cat in SKILL_CATEGORY_IDS if cat in set(manual)]
                result[name] = {
                    "scenario": name,
                    "categories": ordered,
                    "source": "manual",
                    "auto_categories": auto_tags,
                }
            else:
                result[name] = {
                    "scenario": name,
                    "categories": auto_tags,
                    "source": "auto",
                    "auto_categories": auto_tags,
                }
        return result

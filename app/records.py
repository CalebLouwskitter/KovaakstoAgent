"""Personal bests (PBs) and practice streaks query layer for Kovaak Agent.

Computes:
- Personal best per scenario with best score, date set, and previous best.
- PBs set in a given calendar period (day, week, month, year).
- Current and longest practice streaks in consecutive calendar days.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Iterable, Iterator

from .memory import PERIODS, period_bounds

PERIOD_ALIASES = {
    "day": "daily",
    "daily": "daily",
    "week": "weekly",
    "weekly": "weekly",
    "month": "monthly",
    "monthly": "monthly",
    "year": "yearly",
    "yearly": "yearly",
}


def normalize_period(period: str) -> str:
    """Normalize period alias ('week', 'day', etc.) to standard review period names."""
    cleaned = (period or "").strip().lower()
    if cleaned not in PERIOD_ALIASES:
        raise ValueError("Choose daily, weekly, monthly, or yearly.")
    return PERIOD_ALIASES[cleaned]


@contextmanager
def _connection(db_or_conn: Any) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection whether given a Database instance or a raw Connection."""
    if hasattr(db_or_conn, "connect"):
        with db_or_conn.connect() as conn:
            yield conn
    else:
        yield db_or_conn


def calculate_streaks(dates: Iterable[date], as_of: date | str | None = None) -> dict[str, int]:
    """Calculate current and longest streaks in consecutive calendar days.

    Args:
        dates: Collection of dates on which at least one run was completed.
        as_of: Reference date for current streak calculation (defaults to date.today()).

    Returns:
        Dictionary with 'current' and 'longest' streak counts in days.
    """
    unique_dates = sorted(set(dates))
    if not unique_dates:
        return {"current": 0, "longest": 0}

    # Longest streak across all recorded history
    longest = 0
    current_run = 0
    prev_date: date | None = None
    for d in unique_dates:
        if prev_date is None or d == prev_date + timedelta(days=1):
            current_run += 1
        else:
            current_run = 1
        if current_run > longest:
            longest = current_run
        prev_date = d

    # Reference date for current streak
    if as_of is None:
        target_date = date.today()
    elif isinstance(as_of, str):
        target_date = date.fromisoformat(as_of[:10])
    elif isinstance(as_of, date):
        target_date = as_of
    else:
        target_date = date.today()

    date_set = set(unique_dates)
    current = 0
    check = target_date
    if check not in date_set:
        check = target_date - timedelta(days=1)

    while check in date_set:
        current += 1
        check -= timedelta(days=1)

    return {"current": current, "longest": longest}


def _analyze_runs(rows: Iterable[sqlite3.Row | dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Analyze chronological runs to extract all PB events and latest PBs per scenario.

    Ties do not count as new personal bests: a run must strictly exceed the prior best.
    """
    all_pb_events: list[dict[str, Any]] = []
    current_bests: dict[str, dict[str, Any]] = {}

    for row in rows:
        scenario = row["scenario"]
        if not scenario or not str(scenario).strip():
            continue
        scenario = str(scenario).strip()
        if row["score"] is None:
            continue
        score = float(row["score"])
        observed_at = str(row["observed_at"])
        run_id = row["id"] if "id" in (row.keys() if hasattr(row, "keys") else row) else None

        if scenario not in current_bests:
            pb_event = {
                "id": run_id,
                "scenario": scenario,
                "score": score,
                "new_best": score,
                "previous_score": None,
                "previous_best": None,
                "delta": None,
                "observed_at": observed_at,
                "date": observed_at[:10],
            }
            current_bests[scenario] = pb_event
            all_pb_events.append(pb_event)
        else:
            prev_best = current_bests[scenario]["score"]
            if score > prev_best:
                delta = round(score - prev_best, 4)
                if delta.is_integer():
                    delta = int(delta)
                pb_event = {
                    "id": run_id,
                    "scenario": scenario,
                    "score": score,
                    "new_best": score,
                    "previous_score": prev_best,
                    "previous_best": prev_best,
                    "delta": delta,
                    "observed_at": observed_at,
                    "date": observed_at[:10],
                }
                current_bests[scenario] = pb_event
                all_pb_events.append(pb_event)

    return all_pb_events, current_bests


def get_all_personal_bests(db_or_conn: Any) -> list[dict[str, Any]]:
    """Return the all-time personal best per scenario, ordered by scenario name."""
    with _connection(db_or_conn) as conn:
        rows = conn.execute(
            """
            SELECT id, scenario, score, observed_at
            FROM runs
            WHERE scenario IS NOT NULL AND TRIM(scenario) != '' AND score IS NOT NULL
            ORDER BY observed_at ASC, id ASC
            """
        ).fetchall()
    _, current_bests = _analyze_runs(rows)
    return sorted(current_bests.values(), key=lambda item: item["scenario"].lower())


def get_streaks(db_or_conn: Any, as_of: date | str | None = None) -> dict[str, int]:
    """Return practice streaks (current and longest) across all runs in the database."""
    with _connection(db_or_conn) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT substr(observed_at, 1, 10) AS run_date
            FROM runs
            WHERE scenario IS NOT NULL AND TRIM(scenario) != '' AND score IS NOT NULL
            ORDER BY run_date ASC
            """
        ).fetchall()
    dates = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row["run_date"]))
        except (ValueError, TypeError):
            continue
    return calculate_streaks(dates, as_of=as_of)


def get_period_personal_bests(
    db_or_conn: Any,
    period: str = "week",
    anchor: str | date | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """Return all PBs set within the specified period, ordered by observed_at DESC."""
    norm = normalize_period(period)
    anchor_str = anchor.isoformat() if isinstance(anchor, date) else anchor
    start, end, _ = period_bounds(norm, anchor_str)

    with _connection(db_or_conn) as conn:
        rows = conn.execute(
            """
            SELECT id, scenario, score, observed_at
            FROM runs
            WHERE scenario IS NOT NULL AND TRIM(scenario) != '' AND score IS NOT NULL
            ORDER BY observed_at ASC, id ASC
            """
        ).fetchall()

    all_pb_events, _ = _analyze_runs(rows)
    period_events = [
        event for event in all_pb_events
        if start <= event["date"] < end
    ]
    period_events.sort(key=lambda item: (item["observed_at"], item["id"] or 0), reverse=True)
    return period_events, start, end


def get_records(
    db_or_conn: Any,
    period: str = "week",
    anchor: str | date | None = None,
) -> dict[str, Any]:
    """Return comprehensive records payload: streaks and PBs set in the specified period."""
    norm = normalize_period(period)
    anchor_str = anchor.isoformat() if isinstance(anchor, date) else anchor
    period_events, start, end = get_period_personal_bests(db_or_conn, norm, anchor_str)
    streaks = get_streaks(db_or_conn, as_of=anchor_str)
    all_time = get_all_personal_bests(db_or_conn)

    return {
        "period": norm,
        "start": start,
        "end_exclusive": end,
        "current_streak": streaks["current"],
        "longest_streak": streaks["longest"],
        "streaks": streaks,
        "records": period_events,
        "personal_bests": period_events,
        "pbs": period_events,
        "all_time_pbs": all_time,
    }

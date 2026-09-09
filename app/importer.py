"""CSV ingestion, parsing, and normalization pipeline for Kovaak Agent.

Supports three formats produced by aim trainers and logging tools:
1. Native Kovaak's export: A complex multi-section file containing a per-kill table,
   a weapon performance table, and a key-value summary with aim settings.
2. Standard tabular CSV: A classic table with column headers and multiple run rows.
3. Key-value CSV: Two-column configuration or single-run summary files.

Every format is parsed and normalized into standard run metrics (score, accuracy,
shots, hits, weapon, sensitivity, DPI, FPS) with a deterministic SHA-256 row hash.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .database import Database


# Common alias mappings to tolerate slight header variations across game patches
SCENARIO_KEYS = ("scenario", "scenario name", "drill", "map")
TIME_KEYS = ("timestamp", "date played", "played at", "date", "time")
SCORE_KEYS = ("score", "final score", "high score")
ACCURACY_KEYS = ("accuracy", "accuracy %", "acc")
DURATION_KEYS = ("duration", "duration seconds", "time played", "session time", "length")
SHOT_KEYS = ("shots", "weapon shots")
HIT_KEYS = ("hit count", "hits", "weapon hits")
MISS_KEYS = ("miss count", "misses")


@dataclass(frozen=True)
class ImportResult:
    """Summary metrics returned after ingesting a single CSV file."""
    path: str
    mode: str
    rows_seen: int
    inserted: int
    updated: int
    unchanged: int
    removed: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _read_text(path: Path) -> str:
    """Read file content attempting common Windows and UTF-8 encodings before falling back with replacement."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _dialect(text: str) -> csv.Dialect:
    """Sniff CSV delimiter (comma, semicolon, tab, pipe) or fall back to Excel defaults."""
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _clean_key(value: str) -> str:
    """Normalize dictionary keys: strip colons, outer spaces, and condense internal whitespace."""
    return re.sub(r"\s+", " ", value.strip().strip(":"))


def _rows(text: str) -> list[list[str]]:
    """Parse CSV text into stripped non-empty rows using detected dialect."""
    reader = csv.reader(io.StringIO(text), dialect=_dialect(text))
    return [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]


def _looks_tabular(rows: list[list[str]]) -> bool:
    """Check if rows look like a traditional multi-row table (consistent column count and textual headers)."""
    if len(rows) < 2 or len(rows[0]) < 2:
        return False
    width = len(rows[0])
    consistent = sum(1 for row in rows[1:10] if len(row) == width)
    header = rows[0]
    textual_headers = sum(1 for cell in header if cell and not _to_float(cell))
    return consistent >= max(1, min(len(rows) - 1, 3)) and textual_headers >= max(1, width // 2)


def _looks_key_value(rows: list[list[str]]) -> bool:
    """Check if rows represent a key-value export (e.g. 'Scenario:,Tile Frenzy')."""
    known = set(SCENARIO_KEYS + TIME_KEYS + SCORE_KEYS + ACCURACY_KEYS + DURATION_KEYS)
    first_cells = [_clean_key(row[0]).lower() for row in rows if row]
    known_matches = sum(1 for cell in first_cells if cell in known)
    colon_labels = sum(1 for row in rows if row and row[0].strip().endswith(":"))
    two_column_rows = sum(1 for row in rows if 2 <= len(row) <= 3)
    return known_matches >= 2 or (
        len(rows) >= 2
        and colon_labels >= max(2, len(rows) // 2)
        and two_column_rows >= max(2, len(rows) // 2)
    )


def _looks_kovaaks_stats(rows: list[list[str]]) -> bool:
    """Identify native Kovaak's export containing kill table, weapon summary, and challenge settings."""
    labels = {_clean_key(row[0]).lower() for row in rows if row}
    has_kill_table = any(row and _clean_key(row[0]).lower() == "kill #" for row in rows)
    return has_kill_table and "scenario" in labels and "score" in labels and "weapon" in labels


def _parse_kovaaks_stats(rows: list[list[str]]) -> dict[str, str]:
    """Flatten Kovaak's multi-section run export into one auditable record."""
    record: dict[str, str] = {}

    # Kovaak's summary and settings blocks use ``Label:,value`` rows.
    for row in rows:
        if not row or not row[0].strip().endswith(":"):
            continue
        key = _clean_key(row[0])
        value = ", ".join(cell for cell in row[1:] if cell)
        record[key] = value

    # The weapon block is a small table between the per-kill and summary blocks.
    weapon_header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row and _clean_key(row[0]).lower() == "weapon" and not row[0].endswith(":")
        ),
        None,
    )
    if weapon_header_index is not None:
        headers = [_clean_key(cell) for cell in rows[weapon_header_index]]
        weapon_rows: list[dict[str, str]] = []
        for row in rows[weapon_header_index + 1 :]:
            if not row or row[0].strip().endswith(":"):
                break
            values = row + [""] * (len(headers) - len(row))
            weapon_rows.append(
                {headers[index]: values[index] for index in range(len(headers)) if headers[index]}
            )
        if weapon_rows:
            primary = weapon_rows[0]
            record["Weapon"] = primary.get("Weapon", "")
            for key, value in primary.items():
                if key != "Weapon" and value != "":
                    record[f"Weapon {key}"] = value
            record["Weapon Rows"] = json.dumps(weapon_rows, ensure_ascii=False, separators=(",", ":"))

    # Preserve any per-kill detail without treating each kill as a separate run.
    kill_header_index = next(
        (index for index, row in enumerate(rows) if row and _clean_key(row[0]).lower() == "kill #"),
        None,
    )
    if kill_header_index is not None and weapon_header_index is not None and weapon_header_index > kill_header_index:
        headers = [_clean_key(cell) for cell in rows[kill_header_index]]
        kill_rows = []
        for row in rows[kill_header_index + 1 : weapon_header_index]:
            if not row:
                continue
            values = row + [""] * (len(headers) - len(row))
            kill_rows.append({headers[index]: values[index] for index in range(len(headers)) if headers[index]})
        if kill_rows:
            record["Kill Rows"] = json.dumps(kill_rows, ensure_ascii=False, separators=(",", ":"))

    return record


def parse_csv(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Parse a CSV file into a format mode ('kovaaks_stats', 'table', or 'key_value') and a list of records."""
    rows = _rows(_read_text(path))
    if not rows:
        return "empty", []

    # 1. Check for native multi-section Kovaak's export with kill and weapon tables
    if _looks_kovaaks_stats(rows):
        return "kovaaks_stats", [_parse_kovaaks_stats(rows)]

    # 2. Check for standard tabular CSV (e.g. headers on row 0, data on rows 1..N)
    if not _looks_key_value(rows) and _looks_tabular(rows):
        headers = []
        seen: dict[str, int] = {}
        for index, cell in enumerate(rows[0], start=1):
            base = _clean_key(cell) or f"column_{index}"
            seen[base] = seen.get(base, 0) + 1
            headers.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        records = []
        for row in rows[1:]:
            padded = row + [""] * (len(headers) - len(row))
            records.append({headers[i]: padded[i] for i in range(len(headers))})
        return "table", records

    # 3. Fall back to key-value structure where col 0 is key, rest is value
    record: dict[str, str] = {}
    repeats: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        key = _clean_key(row[0]) if row else f"row_{index}"
        value = ", ".join(cell for cell in row[1:] if cell) if len(row) > 1 else ""
        if not key:
            key = f"row_{index}"
        repeats[key] = repeats.get(key, 0) + 1
        final_key = key if repeats[key] == 1 else f"{key}_{repeats[key]}"
        record[final_key] = value
    return "key_value", [record]


def _lookup(record: dict[str, str], aliases: Iterable[str]) -> str | None:
    """Find the first matching non-empty value in record matching any of the alias names."""
    lowered = {_clean_key(key).lower(): value for key, value in record.items()}
    for alias in aliases:
        value = lowered.get(alias)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: str | None) -> float | None:
    """Safely parse a string into float, stripping '%' and handling commas and decimal points."""
    if value is None:
        return None
    cleaned = value.strip().replace("%", "").replace(" ", "")
    if not cleaned:
        return None
    if cleaned.count(",") == 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _duration_seconds(value: str | None) -> float | None:
    """Parse duration strings in seconds or 'HH:MM:SS' / 'MM:SS' time format into seconds."""
    direct = _to_float(value)
    if direct is not None:
        return direct
    if not value:
        return None
    parts = value.strip().split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return None


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y.%m.%d-%H.%M.%S",
    "%Y-%m-%d",
)


def _observed_at(value: str | None, path: Path) -> str:
    """Determine when the run occurred: from record field, filename timestamp, or file mtime."""
    if value:
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            pass
        for pattern in DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), pattern).isoformat(timespec="seconds")
            except ValueError:
                continue

    # Try extracting timestamp from filename pattern (e.g. '... - 2026.08.30-18.05.00 Stats.csv')
    filename_match = re.search(r"(20\d{2}[.-]\d{2}[.-]\d{2})[-_ ](\d{2}[.-]\d{2}[.-]\d{2})", path.stem)
    if filename_match:
        candidate = f"{filename_match.group(1)}-{filename_match.group(2)}".replace(".", "-")
        try:
            return datetime.strptime(candidate, "%Y-%m-%d-%H-%M-%S").isoformat(timespec="seconds")
        except ValueError:
            pass
    # Fallback to file filesystem modification timestamp
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _run_duration(record: dict[str, str], observed_at: str) -> float | None:
    """Extract challenge duration in seconds from duration fields, fight time, or start/finish diff."""
    explicit = _duration_seconds(_lookup(record, DURATION_KEYS))
    if explicit and explicit > 0:
        return explicit
    fight_time = _duration_seconds(_lookup(record, ("fight time",)))
    if fight_time and fight_time > 0:
        return fight_time
    challenge_start = _lookup(record, ("challenge start",))
    if not challenge_start:
        return None
    try:
        finished = datetime.fromisoformat(observed_at)
        started_time = datetime.strptime(challenge_start.strip(), "%H:%M:%S.%f").time()
        started = datetime.combine(finished.date(), started_time)
        if started > finished:
            started -= timedelta(days=1)
        duration = (finished - started).total_seconds()
        return duration if 0 < duration <= 86400 else None
    except ValueError:
        return None


def _scenario(record: dict[str, str], path: Path) -> str:
    """Extract scenario name from record or derive it from the CSV filename stem."""
    explicit = _lookup(record, SCENARIO_KEYS)
    if explicit:
        return explicit
    stem = re.sub(r"\s+-\s+(Challenge|Free Play|Stats).*$", "", path.stem, flags=re.IGNORECASE)
    return stem.strip()


def normalize(record: dict[str, str], path: Path, source_row: int) -> dict[str, Any]:
    """Map raw key-value pairs into a canonical database run record with computed metrics."""
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    observed_at = _observed_at(_lookup(record, TIME_KEYS), path)
    shots = _to_float(_lookup(record, SHOT_KEYS))
    hits = _to_float(_lookup(record, HIT_KEYS))
    misses = _to_float(_lookup(record, MISS_KEYS))
    # Infer missing shot or miss counts when possible
    if shots is None and hits is not None and misses is not None:
        shots = hits + misses
    if misses is None and shots is not None and hits is not None:
        misses = max(0.0, shots - hits)
    # Compute accuracy percentage if not explicitly provided
    accuracy = _to_float(_lookup(record, ACCURACY_KEYS))
    if accuracy is None and hits is not None and shots:
        accuracy = hits / shots * 100
    damage_done = _to_float(_lookup(record, ("damage done", "weapon damage done")))
    damage_possible = _to_float(_lookup(record, ("weapon damage possible", "damage possible")))
    efficiency = _to_float(_lookup(record, ("efficiency", "weapon efficiency")))
    if efficiency is None and damage_done is not None and damage_possible:
        efficiency = damage_done / damage_possible * 100
    return {
        "source_path": str(path.resolve()),
        "source_row": source_row,
        "row_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "observed_at": observed_at,
        "timestamp_inferred": not _lookup(record, TIME_KEYS) and not re.search(
            r"(20\d{2}[.-]\d{2}[.-]\d{2})[-_ ](\d{2}[.-]\d{2}[.-]\d{2})", path.stem),
        "scenario": _scenario(record, path),
        "score": _to_float(_lookup(record, SCORE_KEYS)),
        "accuracy": accuracy,
        "duration_seconds": _run_duration(record, observed_at),
        "shots": shots,
        "hits": hits,
        "misses": misses,
        "damage_done": damage_done,
        "damage_possible": damage_possible,
        "efficiency": efficiency,
        "weapon": _lookup(record, ("weapon",)),
        "sensitivity": _to_float(_lookup(record, ("horiz sens",))),
        "sensitivity_scale": _lookup(record, ("sens scale",)),
        "dpi": _to_float(_lookup(record, ("dpi",))),
        "fov": _to_float(_lookup(record, ("fov",))),
        "fov_scale": _lookup(record, ("fovscale", "fov scale")),
        "avg_fps": _to_float(_lookup(record, ("avg fps",))),
        "resolution": _lookup(record, ("resolution",)),
        "game_version": _lookup(record, ("game version",)),
        "raw": record,
    }


def import_file(database: Database, path: str | Path) -> ImportResult:
    """Commit a complete source capture atomically; keep failures retryable."""
    try:
        with database.connect():
            return _import_file(database, path)
    except Exception as exc:
        csv_path = Path(path).expanduser().resolve()
        if csv_path.is_file():
            stat = csv_path.stat()
            database.upsert_source(str(csv_path), stat.st_size, stat.st_mtime_ns, "error", str(exc))
        raise


def _import_file(database: Database, path: str | Path) -> ImportResult:
    """Ingest a single CSV file, updating sources and runs tables with change counts."""
    csv_path = Path(path).expanduser().resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    stat = csv_path.stat()
    database.upsert_source(str(csv_path), stat.st_size, stat.st_mtime_ns, "importing")
    try:
        mode, records = parse_csv(csv_path)
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        occurrences = {}
        content_occurrences = {}
        for index, record in enumerate(records, start=1):
            run = normalize(record, csv_path, index)
            identity = (run["observed_at"], run.get("scenario") or "Unknown")
            occurrences[identity] = occurrences.get(identity, 0) + 1
            run["event_ordinal"] = occurrences[identity]
            content_occurrences[run["row_hash"]] = content_occurrences.get(run["row_hash"], 0) + 1
            run["content_ordinal"] = content_occurrences[run["row_hash"]]
            outcome = database.upsert_run(run)
            counts[outcome] += 1
        removed = database.delete_rows_after(str(csv_path), len(records))
        database.finish_source(str(csv_path), stat.st_size, stat.st_mtime_ns, len(records))
        return ImportResult(
            path=str(csv_path),
            mode=mode,
            rows_seen=len(records),
            inserted=counts["inserted"],
            updated=counts["updated"],
            unchanged=counts["unchanged"],
            removed=removed,
        )
    except Exception as exc:
        database.upsert_source(str(csv_path), stat.st_size, stat.st_mtime_ns, "error", str(exc))
        raise


def discover_csv_files(path: str | Path) -> list[Path]:
    """Return all .csv files from a single file path or recursively from a directory, sorted by mtime descending."""
    candidate = Path(path).expanduser()
    if candidate.is_file():
        return [candidate] if candidate.suffix.lower() == ".csv" else []
    if not candidate.is_dir():
        return []
    return sorted(candidate.rglob("*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)


def import_path(database: Database, path: str | Path, force: bool = False) -> list[ImportResult]:
    """Scan and import all CSV files at the specified path, skipping files with unchanged signatures."""
    results = []
    for csv_path in discover_csv_files(path):
        stat = csv_path.stat()
        signature = database.source_signature(str(csv_path.resolve()))
        if not force and signature == (stat.st_size, stat.st_mtime_ns):
            continue
        results.append(import_file(database, csv_path))
    return results

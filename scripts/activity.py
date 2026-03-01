from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

PROGRAMS_FILE = DATA_DIR / "programs.json"
CHANGES_FILE = DATA_DIR / "changes.json"
ACTIVITY_FILE = DATA_DIR / "activity.json"

RECENT_SUBMISSION_DAYS = 45
HIGH_SUBMISSION_VOLUME_THRESHOLD = 300
MAX_ACTIVITY_ITEMS = 320


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def build_change_summary(change: dict[str, Any]) -> str:
    change_type = str(change.get("type", "")).strip()
    if change_type == "new_program":
        return "Program added to the dataset."
    if change_type == "program_removed":
        return "Program removed from the dataset."
    if change_type == "bounty_changed":
        return "Bounty range changed."
    if change_type == "scope_added":
        after = change.get("after", [])
        count = len(after) if isinstance(after, list) else 0
        return f"Scope expanded ({count} new target{'s' if count != 1 else ''})."
    if change_type == "scope_removed":
        before = change.get("before", [])
        count = len(before) if isinstance(before, list) else 0
        return f"Scope reduced ({count} target{'s' if count != 1 else ''} removed)."
    if change_type == "asset_type_changed":
        details = change.get("details", {})
        if isinstance(details, dict):
            added = details.get("added", [])
            removed = details.get("removed", [])
            added_count = len(added) if isinstance(added, list) else 0
            removed_count = len(removed) if isinstance(removed, list) else 0
            return f"Asset types updated (+{added_count} / -{removed_count})."
        return "Asset types changed."
    return "Program activity updated."


def build_change_events(
    changes: list[dict[str, Any]],
    program_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue

        program_id = str(change.get("programId", "")).strip()
        program = program_index.get(program_id, {})

        timestamp = str(change.get("timestamp", "")).strip() or now_iso()
        platform = str(change.get("platform", "")).strip() or str(program.get("platform", "Unknown"))
        program_name = str(change.get("programName", "")).strip() or str(program.get("name", "Unknown Program"))
        change_type = str(change.get("type", "")).strip() or "program_updated"

        events.append(
            {
                "id": f"change-{change_type}-{program_id}-{timestamp}",
                "timestamp": timestamp,
                "type": change_type,
                "platform": platform,
                "programId": program_id,
                "programName": program_name,
                "programUrl": str(program.get("url", "")).strip(),
                "summary": build_change_summary(change),
            }
        )
    return events


def build_submission_events(
    programs: list[dict[str, Any]],
    generated_at: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    generated_at_dt = parse_iso(generated_at)

    for program in programs:
        if not isinstance(program, dict):
            continue

        program_id = str(program.get("id", "")).strip()
        if not program_id:
            continue

        platform = str(program.get("platform", "Unknown")).strip()
        program_name = str(program.get("name", "Unknown Program")).strip()
        program_url = str(program.get("url", "")).strip()
        last_submission_at = str(program.get("lastSubmissionAt", "")).strip()
        submission_count = program.get("submissionCount")
        submissions_last_7d = program.get("submissionsLast7d")

        if last_submission_at:
            submission_dt = parse_iso(last_submission_at)
            age_days = max(0, (generated_at_dt - submission_dt).days)
            if age_days <= RECENT_SUBMISSION_DAYS:
                if isinstance(submissions_last_7d, int) and submissions_last_7d > 0:
                    summary = f"Recent submission activity observed ({submissions_last_7d} in last 7d)."
                else:
                    summary = "Recent submission activity observed."
                events.append(
                    {
                        "id": f"submission-recent-{program_id}-{last_submission_at}",
                        "timestamp": last_submission_at,
                        "type": "recent_submission",
                        "platform": platform,
                        "programId": program_id,
                        "programName": program_name,
                        "programUrl": program_url,
                        "summary": summary,
                    }
                )

        if isinstance(submission_count, int) and submission_count >= HIGH_SUBMISSION_VOLUME_THRESHOLD:
            events.append(
                {
                    "id": f"submission-volume-{program_id}-{submission_count}",
                    "timestamp": str(program.get("lastUpdated", "")).strip() or generated_at,
                    "type": "high_submission_volume",
                    "platform": platform,
                    "programId": program_id,
                    "programName": program_name,
                    "programUrl": program_url,
                    "summary": f"High submission volume signal ({submission_count} total submissions).",
                }
            )

    return events


def main() -> None:
    programs_payload = load_json(PROGRAMS_FILE)
    changes_payload = load_json(CHANGES_FILE)

    generated_at = str(programs_payload.get("generatedAt", now_iso()))
    programs = programs_payload.get("programs", [])
    changes = changes_payload.get("items", [])
    if not isinstance(programs, list):
        programs = []
    if not isinstance(changes, list):
        changes = []

    program_index = {
        str(program.get("id", "")): program
        for program in programs
        if isinstance(program, dict) and program.get("id")
    }

    events: list[dict[str, Any]] = []
    events.extend(build_change_events(changes, program_index))
    events.extend(build_submission_events(programs, generated_at))

    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        if isinstance(event, dict):
            deduped[event["id"]] = event

    ordered_events = sorted(
        deduped.values(),
        key=lambda item: parse_iso(str(item.get("timestamp", ""))),
        reverse=True,
    )
    limited_events = ordered_events[:MAX_ACTIVITY_ITEMS]

    by_type = Counter(str(item.get("type", "unknown")) for item in limited_events)
    by_platform = Counter(str(item.get("platform", "Unknown")) for item in limited_events)

    payload = {
        "generatedAt": generated_at,
        "totalEvents": len(ordered_events),
        "items": limited_events,
        "byType": [
            {"type": key, "count": count}
            for key, count in sorted(by_type.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "byPlatform": [
            {"platform": key, "count": count}
            for key, count in sorted(by_platform.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    }

    write_json(ACTIVITY_FILE, payload)
    print(f"[activity] wrote {ACTIVITY_FILE} ({len(limited_events)} of {len(ordered_events)} events)")


if __name__ == "__main__":
    main()


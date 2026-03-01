from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

PROGRAMS_FILE = DATA_DIR / "programs.json"
CHANGES_FILE = DATA_DIR / "changes.json"
LATEST_UPDATES_FILE = DATA_DIR / "latest_updates.json"
CHANGE_LOG_FILE = DATA_DIR / "history" / "changes.log.json"

WINDOW_DAYS = 7
MAX_UPDATE_ITEMS = 2000
TRACKED_CHANGE_TYPES = {
    "new_program",
    "scope_added",
    "scope_removed",
}
EXCLUDED_PLATFORMS = {"Independent"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def stable_change_id(change: Dict[str, Any]) -> str:
    blob = json.dumps(
        {
            "timestamp": change.get("timestamp"),
            "type": change.get("type"),
            "programId": change.get("programId"),
            "before": change.get("before"),
            "after": change.get("after"),
            "details": change.get("details"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def to_logged_change(change: Dict[str, Any], generated_at: str) -> Dict[str, Any] | None:
    change_type = str(change.get("type", "")).strip()
    if change_type not in TRACKED_CHANGE_TYPES:
        return None
    platform = str(change.get("platform", "")).strip()
    if platform in EXCLUDED_PLATFORMS:
        return None
    timestamp = str(change.get("timestamp", "")).strip() or generated_at
    item_id = stable_change_id(change)
    return {
        "id": item_id,
        "timestamp": timestamp,
        "type": change_type,
        "programId": str(change.get("programId", "")).strip(),
        "programName": str(change.get("programName", "")).strip(),
        "platform": platform,
        "before": change.get("before"),
        "after": change.get("after"),
        "details": change.get("details", {}),
    }


def build_program_index(programs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(program.get("id", "")).strip(): program
        for program in programs
        if isinstance(program, dict) and program.get("id")
    }


def build_update_items(
    logged_changes: list[dict[str, Any]],
    program_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for change in logged_changes:
        if not isinstance(change, dict):
            continue

        change_type = str(change.get("type", "")).strip()
        timestamp = str(change.get("timestamp", "")).strip() or now_iso()
        program_id = str(change.get("programId", "")).strip()
        program = program_index.get(program_id, {})

        program_name = str(change.get("programName", "")).strip() or str(program.get("name", "Unknown Program"))
        platform = str(change.get("platform", "")).strip() or str(program.get("platform", "Unknown"))
        program_url = str(program.get("url", "")).strip()
        program_category = str(program.get("programCategory", "Unknown")).strip() or "Unknown"

        if change_type == "new_program":
            items.append(
                {
                    "id": f"latest-new-{change.get('id')}",
                    "type": "new_program",
                    "timestamp": timestamp,
                    "programId": program_id or None,
                    "programName": program_name,
                    "platform": platform,
                    "programUrl": program_url,
                    "summary": "Program was added in the last 7 days.",
                    "program": {
                        "category": program_category,
                        "bountyType": program.get("bountyType"),
                        "bountyRange": program.get("bountyRange"),
                        "priorityScore": program.get("priorityScore"),
                        "scopeSummary": program.get("scopeSummary"),
                        "assetTypes": program.get("assetTypes", []),
                        "isIndiaRelevant": program.get("isIndiaRelevant", False),
                    },
                    "scopeChange": None,
                }
            )
            continue

        if change_type in {"scope_added", "scope_removed"}:
            targets_raw = change.get("after") if change_type == "scope_added" else change.get("before")
            if isinstance(targets_raw, list):
                targets = [str(target).strip() for target in targets_raw if str(target).strip()]
            else:
                targets = []

            direction = "added" if change_type == "scope_added" else "removed"
            target_count = len(targets)
            target_label = f"{target_count} target{'s' if target_count != 1 else ''}"

            items.append(
                {
                    "id": f"latest-scope-{change.get('id')}",
                    "type": "scope_update",
                    "timestamp": timestamp,
                    "programId": program_id or None,
                    "programName": program_name,
                    "platform": platform,
                    "programUrl": program_url,
                    "summary": f"Scope {direction}: {target_label}.",
                    "program": {
                        "category": program_category,
                        "bountyType": program.get("bountyType"),
                        "bountyRange": program.get("bountyRange"),
                        "priorityScore": program.get("priorityScore"),
                        "scopeSummary": program.get("scopeSummary"),
                        "assetTypes": program.get("assetTypes", []),
                        "isIndiaRelevant": program.get("isIndiaRelevant", False),
                    },
                    "scopeChange": {
                        "direction": direction,
                        "targets": targets[:20],
                        "count": target_count,
                    },
                }
            )

    return items


def main() -> None:
    programs_payload = load_json(PROGRAMS_FILE)
    changes_payload = load_json(CHANGES_FILE)
    existing_log_payload = load_json(CHANGE_LOG_FILE)

    generated_at = str(programs_payload.get("generatedAt", now_iso()))
    generated_dt = parse_iso(generated_at)
    window_start_dt = generated_dt - timedelta(days=WINDOW_DAYS)

    programs = programs_payload.get("programs", [])
    if not isinstance(programs, list):
        programs = []
    changes = changes_payload.get("items", [])
    if not isinstance(changes, list):
        changes = []

    existing_items = existing_log_payload.get("items", [])
    if not isinstance(existing_items, list):
        existing_items = []

    merged: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if str(item.get("platform", "")).strip() in EXCLUDED_PLATFORMS:
            continue
        if parse_iso(str(item.get("timestamp", ""))) < window_start_dt:
            continue
        if str(item.get("type", "")).strip() not in TRACKED_CHANGE_TYPES:
            continue
        merged[str(item["id"])] = item

    for change in changes:
        if not isinstance(change, dict):
            continue
        logged = to_logged_change(change, generated_at)
        if logged is None:
            continue
        merged[str(logged["id"])] = logged

    pruned_changes = [
        item
        for item in merged.values()
        if parse_iso(str(item.get("timestamp", ""))) >= window_start_dt
        and str(item.get("platform", "")).strip() not in EXCLUDED_PLATFORMS
    ]
    pruned_changes.sort(key=lambda item: parse_iso(str(item.get("timestamp", ""))), reverse=True)

    program_index = build_program_index(programs)
    update_items = build_update_items(pruned_changes, program_index)
    update_items.sort(key=lambda item: parse_iso(str(item.get("timestamp", ""))), reverse=True)
    limited_items = update_items[:MAX_UPDATE_ITEMS]

    type_counter = Counter(str(item.get("type", "")) for item in limited_items)
    platform_counter = Counter(str(item.get("platform", "Unknown")) for item in limited_items)
    scope_programs = {
        str(item.get("programId", ""))
        for item in limited_items
        if str(item.get("type", "")) == "scope_update" and item.get("programId")
    }

    log_payload = {
        "generatedAt": generated_at,
        "windowDays": WINDOW_DAYS,
        "windowStart": iso(window_start_dt),
        "items": pruned_changes,
    }
    write_json(CHANGE_LOG_FILE, log_payload)

    latest_updates_payload = {
        "generatedAt": generated_at,
        "windowDays": WINDOW_DAYS,
        "windowStart": iso(window_start_dt),
        "summary": {
            "totalItems": len(limited_items),
            "newPrograms": type_counter.get("new_program", 0),
            "scopeUpdates": type_counter.get("scope_update", 0),
            "programsWithScopeUpdates": len(scope_programs),
        },
        "items": limited_items,
        "byPlatform": [
            {"platform": platform, "count": count}
            for platform, count in sorted(platform_counter.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "byType": [
            {"type": item_type, "count": count}
            for item_type, count in sorted(type_counter.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    }
    write_json(LATEST_UPDATES_FILE, latest_updates_payload)
    print(f"[latest_updates] wrote {LATEST_UPDATES_FILE} ({len(limited_items)} items)")
    print(f"[latest_updates] wrote {CHANGE_LOG_FILE} ({len(pruned_changes)} log items)")


if __name__ == "__main__":
    main()

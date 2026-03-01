from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

PROGRAMS_FILE = DATA_DIR / "programs.json"
PREVIOUS_FILE = DATA_DIR / "history" / "programs.prev.json"
CHANGES_FILE = DATA_DIR / "changes.json"


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


def get_program_index(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        program["id"]: program
        for program in payload.get("programs", [])
        if isinstance(program, dict) and program.get("id")
    }


def bounty_signature(program: Dict[str, Any]) -> Tuple[str, int, int]:
    return (
        program.get("bountyType", "No bounty"),
        int(program.get("bountyMinUsd", 0)),
        int(program.get("bountyMaxUsd", 0)),
    )


def emit_change(
    changes: list[Dict[str, Any]],
    *,
    change_type: str,
    timestamp: str,
    program: Dict[str, Any],
    before: Any = None,
    after: Any = None,
    details: Dict[str, Any] | None = None,
) -> None:
    changes.append(
        {
            "timestamp": timestamp,
            "type": change_type,
            "programId": program.get("id"),
            "programName": program.get("name"),
            "platform": program.get("platform"),
            "before": before,
            "after": after,
            "details": details or {},
        }
    )


def compare_programs(
    old_program: Dict[str, Any],
    new_program: Dict[str, Any],
    *,
    timestamp: str,
    changes: list[Dict[str, Any]],
) -> None:
    if bounty_signature(old_program) != bounty_signature(new_program):
        emit_change(
            changes,
            change_type="bounty_changed",
            timestamp=timestamp,
            program=new_program,
            before={
                "bountyType": old_program.get("bountyType"),
                "bountyMinUsd": old_program.get("bountyMinUsd"),
                "bountyMaxUsd": old_program.get("bountyMaxUsd"),
            },
            after={
                "bountyType": new_program.get("bountyType"),
                "bountyMinUsd": new_program.get("bountyMinUsd"),
                "bountyMaxUsd": new_program.get("bountyMaxUsd"),
            },
        )

    old_assets = sorted(set(old_program.get("assetTypes", [])))
    new_assets = sorted(set(new_program.get("assetTypes", [])))
    if old_assets != new_assets:
        emit_change(
            changes,
            change_type="asset_type_changed",
            timestamp=timestamp,
            program=new_program,
            before=old_assets,
            after=new_assets,
            details={
                "added": sorted(set(new_assets) - set(old_assets)),
                "removed": sorted(set(old_assets) - set(new_assets)),
            },
        )

    old_in_scope = {item.get("target", "") for item in old_program.get("scope", {}).get("in", [])}
    new_in_scope = {item.get("target", "") for item in new_program.get("scope", {}).get("in", [])}

    scope_added = sorted(new_in_scope - old_in_scope)
    scope_removed = sorted(old_in_scope - new_in_scope)

    if scope_added:
        emit_change(
            changes,
            change_type="scope_added",
            timestamp=timestamp,
            program=new_program,
            after=scope_added,
        )
    if scope_removed:
        emit_change(
            changes,
            change_type="scope_removed",
            timestamp=timestamp,
            program=new_program,
            before=scope_removed,
        )


def main() -> None:
    current_payload = load_json(PROGRAMS_FILE)
    previous_payload = load_json(PREVIOUS_FILE)
    generated_at = current_payload.get("generatedAt")

    current_index = get_program_index(current_payload)
    previous_index = get_program_index(previous_payload)

    changes: list[Dict[str, Any]] = []

    for program_id, program in current_index.items():
        if program_id not in previous_index:
            emit_change(
                changes,
                change_type="new_program",
                timestamp=generated_at,
                program=program,
                after={"priorityScore": program.get("priorityScore")},
            )
            continue
        compare_programs(
            previous_index[program_id],
            program,
            timestamp=generated_at,
            changes=changes,
        )

    for program_id, old_program in previous_index.items():
        if program_id not in current_index:
            emit_change(
                changes,
                change_type="program_removed",
                timestamp=generated_at,
                program=old_program,
                before={"priorityScore": old_program.get("priorityScore")},
            )

    changes.sort(key=lambda item: (item["programName"] or "", item["type"]))
    counter = Counter(change["type"] for change in changes)

    payload = {
        "generatedAt": generated_at,
        "comparedAgainst": previous_payload.get("generatedAt"),
        "summary": {
            "totalChanges": len(changes),
            "newPrograms": counter.get("new_program", 0),
            "removedPrograms": counter.get("program_removed", 0),
            "scopeAdditions": counter.get("scope_added", 0),
            "scopeRemovals": counter.get("scope_removed", 0),
            "bountyChanges": counter.get("bounty_changed", 0),
            "assetTypeChanges": counter.get("asset_type_changed", 0),
        },
        "items": changes,
    }

    write_json(CHANGES_FILE, payload)
    write_json(PREVIOUS_FILE, current_payload)

    print(f"[diff] wrote {CHANGES_FILE} ({len(changes)} changes)")
    print(f"[diff] snapshot updated at {PREVIOUS_FILE}")


if __name__ == "__main__":
    main()

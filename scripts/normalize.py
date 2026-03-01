from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from collectors import get_all_programs

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
PROGRAMS_FILE = DATA_DIR / "programs.json"
STATS_FILE = DATA_DIR / "stats.json"

DOMAIN_PATTERN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$", re.IGNORECASE)
INDIA_DOMAIN_PATTERN = re.compile(r"\.in(?=$|[/:])", re.IGNORECASE)

ASSET_TYPE_MAP = {
    "web": "Web",
    "website": "Web",
    "domain": "Web",
    "api": "API",
    "mobile": "Mobile",
    "android": "Mobile",
    "ios": "Mobile",
    "cloud": "Cloud",
    "iot": "IoT",
    "source": "Source code",
    "source code": "Source code",
    "repo": "Source code",
}

BOUNTY_TYPE_MAP = {
    "cash": "Cash",
    "money": "Cash",
    "points": "Points",
    "swag": "Swag",
    "none": "No bounty",
    "no bounty": "No bounty",
}

ASSET_PRIORITY_WEIGHTS = {
    "Web": 5,
    "API": 10,
    "Mobile": 7,
    "Cloud": 8,
    "IoT": 7,
    "Source code": 8,
}

ACTIVE_SUBMISSION_COUNT_THRESHOLD = 20
ACTIVE_LAST_SUBMISSION_DAYS = 45


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def normalize_optional_iso(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None
    return parsed if parsed >= 0 else None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "program"


def normalize_bounty(raw_bounty: Dict[str, Any] | None) -> Tuple[str, int, int, str]:
    bounty = raw_bounty or {}
    bounty_type = BOUNTY_TYPE_MAP.get(str(bounty.get("type", "none")).strip().lower(), "No bounty")

    min_usd = int(bounty.get("min", 0) or 0)
    max_usd = int(bounty.get("max", 0) or 0)

    if bounty_type == "Cash" and max_usd > 0:
        if min_usd > 0:
            range_label = f"${min_usd:,} - ${max_usd:,}"
        else:
            range_label = f"Up to ${max_usd:,}"
    elif bounty_type == "Points":
        range_label = "Points-based rewards"
        min_usd = 0
        max_usd = 0
    elif bounty_type == "Swag":
        range_label = "Swag / gifts"
        min_usd = 0
        max_usd = 0
    else:
        range_label = "No bounty"
        min_usd = 0
        max_usd = 0

    return bounty_type, min_usd, max_usd, range_label


def normalize_asset_type(value: str | None) -> str:
    key = str(value or "web").strip().lower()
    return ASSET_TYPE_MAP.get(key, "Web")


def unique_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_scope(raw_program: Dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw_scope = raw_program.get("scope", {})
    in_scope_items = raw_scope.get("in", [])
    out_scope_items = raw_scope.get("out", [])

    parsed = {
        "wildcardDomains": [],
        "exactDomains": [],
        "urlPaths": [],
        "authFlows": [],
    }
    in_scope: list[dict[str, Any]] = []
    out_scope: list[dict[str, Any]] = []
    asset_types: list[str] = []

    for item in in_scope_items:
        target = str(item.get("target", "")).strip()
        raw_type = str(item.get("type", "other")).strip()
        type_hint = raw_type.lower() or "other"
        auth_required = bool(item.get("auth_required", False))
        notes = str(item.get("notes", "")).strip()
        asset_type = normalize_asset_type(str(item.get("asset_type", "web")))
        asset_types.append(asset_type)

        in_scope.append(
            {
                "target": target,
                "type": raw_type or "other",
                "assetType": asset_type,
                "authRequired": auth_required,
                "notes": notes,
            }
        )

        lowered = target.lower()
        if lowered.startswith("*."):
            parsed["wildcardDomains"].append(target)
        elif lowered.startswith("http://") or lowered.startswith("https://") or "/" in lowered:
            parsed["urlPaths"].append(target)
        elif DOMAIN_PATTERN.match(lowered):
            parsed["exactDomains"].append(target)

        if auth_required or "auth" in type_hint or "oauth" in lowered:
            parsed["authFlows"].append(target)

    for item in out_scope_items:
        out_scope.append(
            {
                "target": str(item.get("target", "")).strip(),
                "reason": str(item.get("reason", "Out of scope")).strip(),
            }
        )

    parsed = {key: unique_preserve(values) for key, values in parsed.items()}

    return {
        "in": in_scope,
        "out": out_scope,
        "parsed": parsed,
    }, sorted(set(asset_types))


def detect_india_relevance(raw_program: Dict[str, Any], normalized_scope: dict[str, Any]) -> tuple[bool, list[str]]:
    signals: list[str] = []
    hq_country = str(raw_program.get("hq_country", "")).strip().upper()
    metadata_regions = [
        str(region).strip().lower() for region in raw_program.get("metadata", {}).get("regions", [])
    ]

    if hq_country in {"IN", "IND", "INDIA"}:
        signals.append("hq_india")

    for item in normalized_scope["in"]:
        if INDIA_DOMAIN_PATTERN.search(item["target"].lower()):
            signals.append("scope_dot_in")
            break

    if any(region in {"india", "in"} for region in metadata_regions):
        signals.append("platform_metadata_india")

    return bool(signals), unique_preserve(signals)


def compute_priority_score(
    in_scope_count: int,
    parsed_scope: dict[str, list[str]],
    asset_types: list[str],
    bounty_max_usd: int,
    updated_at: str,
    metadata: dict[str, Any],
) -> tuple[int, dict[str, int]]:
    now = datetime.now(timezone.utc)
    updated_days = max(0, (now - parse_iso(updated_at)).days)

    scope_breadth = min(in_scope_count * 5, 24)
    wildcard_bonus = 12 if parsed_scope["wildcardDomains"] else 0
    asset_value = min(sum(ASSET_PRIORITY_WEIGHTS.get(asset, 3) for asset in asset_types), 22)

    if bounty_max_usd >= 20000:
        bounty_value = 22
    elif bounty_max_usd >= 10000:
        bounty_value = 18
    elif bounty_max_usd >= 5000:
        bounty_value = 14
    elif bounty_max_usd > 0:
        bounty_value = 9
    else:
        bounty_value = 2

    if updated_days <= 14:
        freshness = 12
    elif updated_days <= 45:
        freshness = 9
    elif updated_days <= 90:
        freshness = 6
    elif updated_days <= 180:
        freshness = 3
    else:
        freshness = 1

    scope_expansion = 8 if bool(metadata.get("recent_scope_expansion")) else 0

    total = min(scope_breadth + wildcard_bonus + asset_value + bounty_value + freshness + scope_expansion, 100)
    breakdown = {
        "scopeBreadth": scope_breadth,
        "wildcardCoverage": wildcard_bonus,
        "assetValue": asset_value,
        "bountyRange": bounty_value,
        "freshness": freshness,
        "recentScopeExpansion": scope_expansion,
    }

    return total, breakdown


def compute_activity_signals(
    submission_count: int | None, last_submission_at: str | None
) -> tuple[bool, list[str]]:
    signals: list[str] = []
    now = datetime.now(timezone.utc)

    if submission_count is not None and submission_count >= ACTIVE_SUBMISSION_COUNT_THRESHOLD:
        signals.append("submission_count")

    if last_submission_at:
        submission_days = max(0, (now - parse_iso(last_submission_at)).days)
        if submission_days <= ACTIVE_LAST_SUBMISSION_DAYS:
            signals.append("recent_submission")

    return bool(signals), signals


def compute_submissions_last_7d(
    submission_count: int | None, last_submission_at: str | None
) -> int | None:
    if submission_count == 0:
        return 0
    if not last_submission_at:
        return None
    now = datetime.now(timezone.utc)
    is_recent = (now - parse_iso(last_submission_at)) <= timedelta(days=7)
    return 1 if is_recent else 0


def infer_program_category(raw_program: Dict[str, Any], bounty_type: str) -> str:
    metadata = raw_program.get("metadata", {}) or {}
    program_kind = str(
        metadata.get("program_kind") or metadata.get("programKind") or ""
    ).strip().upper()
    if program_kind in {"VDP", "BBP"}:
        return program_kind

    listing_type = str(
        metadata.get("listing_type") or metadata.get("listingType") or raw_program.get("program_type") or ""
    ).strip().lower()
    if "security.txt" in listing_type or "vdp" in listing_type or "disclosure" in listing_type:
        return "VDP"
    if "bug bounty" in listing_type or "bbp" in listing_type:
        return "BBP"

    if bounty_type == "No bounty":
        return "VDP"
    if bounty_type in {"Cash", "Points", "Swag"}:
        return "BBP"
    return "Unknown"


def normalize_program(raw_program: Dict[str, Any]) -> Dict[str, Any]:
    platform = str(raw_program.get("platform", "Unknown")).strip()
    source_id = str(raw_program.get("source_id", "")).strip()
    name = str(raw_program.get("name", "Unnamed Program")).strip()
    description = str(raw_program.get("description", "")).strip()
    url = str(raw_program.get("url", "")).strip()
    metadata = raw_program.get("metadata", {}) or {}
    submission_count = parse_non_negative_int(metadata.get("submission_count"))
    last_submission_at = normalize_optional_iso(metadata.get("last_submission_at"))
    actively_hunted, activity_signals = compute_activity_signals(submission_count, last_submission_at)
    submissions_last_7d = compute_submissions_last_7d(submission_count, last_submission_at)

    program_id = f"{slugify(platform)}-{slugify(source_id or name)}"

    bounty_type, bounty_min_usd, bounty_max_usd, bounty_range = normalize_bounty(
        raw_program.get("bounty", {})
    )
    program_category = infer_program_category(raw_program, bounty_type)
    normalized_scope, asset_types = normalize_scope(raw_program)
    india_relevant, india_signals = detect_india_relevance(raw_program, normalized_scope)

    created_at = str(raw_program.get("created_at", now_iso()))
    updated_at = str(raw_program.get("updated_at", created_at))

    score, breakdown = compute_priority_score(
        in_scope_count=len(normalized_scope["in"]),
        parsed_scope=normalized_scope["parsed"],
        asset_types=asset_types,
        bounty_max_usd=bounty_max_usd,
        updated_at=updated_at,
        metadata=metadata,
    )

    placeholder_scope = (
        len(normalized_scope["in"]) == 1
        and normalized_scope["in"][0].get("target") == "public-program-scope"
    )
    if placeholder_scope:
        scope_summary = "Scope data unavailable from source listing"
    else:
        scope_summary = (
            f"{len(normalized_scope['in'])} assets | "
            f"{len(normalized_scope['parsed']['wildcardDomains'])} wildcard | "
            f"{len(asset_types)} asset classes"
        )

    return {
        "id": program_id,
        "programId": program_id,
        "sourceId": source_id,
        "platform": platform,
        "name": name,
        "description": description,
        "url": url,
        "bountyType": bounty_type,
        "programCategory": program_category,
        "bountyMinUsd": bounty_min_usd,
        "bountyMaxUsd": bounty_max_usd,
        "bountyRange": bounty_range,
        "scopeSummary": scope_summary,
        "scope": {
            "in": normalized_scope["in"],
            "out": normalized_scope["out"],
        },
        "scopeParsed": normalized_scope["parsed"],
        "assetTypes": asset_types,
        "isIndiaRelevant": india_relevant,
        "indiaSignals": india_signals,
        "hqCountry": str(raw_program.get("hq_country", "Unknown")).upper(),
        "rules": list(raw_program.get("rules", [])),
        "exclusions": list(raw_program.get("exclusions", [])),
        "createdAt": created_at,
        "lastUpdated": updated_at,
        "priorityScore": score,
        "priorityBreakdown": breakdown,
        "submissionCount": submission_count,
        "submissionsLast7d": submissions_last_7d,
        "lastSubmissionAt": last_submission_at,
        "isActivelyHunted": actively_hunted,
        "activitySignals": activity_signals,
        "metadata": {
            "regions": metadata.get("regions", []),
            "recentScopeExpansion": bool(metadata.get("recent_scope_expansion")),
        },
    }


def build_stats(programs: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    by_platform = Counter(program["platform"] for program in programs)
    by_asset_type: Counter[str] = Counter()
    by_bounty_type = Counter(program["bountyType"] for program in programs)

    for program in programs:
        for asset_type in program["assetTypes"]:
            by_asset_type[asset_type] += 1

    top_priority = sorted(programs, key=lambda item: item["priorityScore"], reverse=True)[:5]

    avg_priority = round(
        sum(program["priorityScore"] for program in programs) / len(programs), 1
    ) if programs else 0

    return {
        "generatedAt": generated_at,
        "totals": {
            "programs": len(programs),
            "indiaRelevant": sum(1 for program in programs if program["isIndiaRelevant"]),
            "cashPrograms": sum(1 for program in programs if program["bountyType"] == "Cash"),
            "avgPriorityScore": avg_priority,
        },
        "byPlatform": [
            {"platform": platform, "count": count}
            for platform, count in sorted(by_platform.items(), key=lambda item: (-item[1], item[0]))
        ],
        "byAssetType": [
            {"assetType": asset_type, "count": count}
            for asset_type, count in sorted(by_asset_type.items(), key=lambda item: (-item[1], item[0]))
        ],
        "byBountyType": [
            {"bountyType": bounty_type, "count": count}
            for bounty_type, count in sorted(by_bounty_type.items(), key=lambda item: (-item[1], item[0]))
        ],
        "topPriorityPrograms": [
            {
                "id": program["id"],
                "name": program["name"],
                "platform": program["platform"],
                "priorityScore": program["priorityScore"],
            }
            for program in top_priority
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def main() -> None:
    generated_at = now_iso()
    raw_programs = get_all_programs()
    normalized = [normalize_program(raw) for raw in raw_programs]
    normalized.sort(key=lambda item: (-item["priorityScore"], -item["bountyMaxUsd"], item["name"]))

    programs_payload = {
        "generatedAt": generated_at,
        "version": "1.0.0",
        "totalPrograms": len(normalized),
        "programs": normalized,
    }
    stats_payload = build_stats(normalized, generated_at)

    write_json(PROGRAMS_FILE, programs_payload)
    write_json(STATS_FILE, stats_payload)

    print(f"[normalize] wrote {PROGRAMS_FILE} ({len(normalized)} programs)")
    print(f"[normalize] wrote {STATS_FILE}")


if __name__ == "__main__":
    main()

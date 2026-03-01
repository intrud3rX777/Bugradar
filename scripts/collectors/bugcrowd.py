from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from urllib.parse import urlencode, urljoin

from .base import (
    RawProgram,
    build_program,
    build_scope_item,
    clean_text,
    dedupe_out_scope_items,
    dedupe_scope_items,
    fetch_json,
    load_seed_programs,
    parse_money_range,
)

ENGAGEMENTS_URL = "https://bugcrowd.com/engagements.json"
CATEGORIES = ("bug_bounty", "vdp")
SORT_BY = "promoted"
SORT_DIRECTION = "desc"
DETAIL_WORKERS = 8


def _fetch_page(category: str, page: int) -> tuple[list[dict], int]:
    params = urlencode(
        {
            "category": category,
            "page": page,
            "sort_by": SORT_BY,
            "sort_direction": SORT_DIRECTION,
        }
    )
    payload = fetch_json(f"{ENGAGEMENTS_URL}?{params}", timeout=45)
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Bugcrowd payload format")

    engagements = payload.get("engagements", [])
    if not isinstance(engagements, list):
        raise ValueError("Unexpected Bugcrowd engagements payload")

    pagination = payload.get("paginationMeta") or {}
    limit = int(pagination.get("limit", 0) or 0)
    total_count = int(pagination.get("totalCount", 0) or 0)

    if total_count > 0 and limit > 0:
        total_pages = max(1, math.ceil(total_count / limit))
    else:
        total_pages = 1 if engagements else 0

    return engagements, total_pages


def _changelog_json_url(source_id: str) -> str:
    return f"https://bugcrowd.com/engagements/{source_id}/changelog.json"


def _detail_changelog_json_url(source_id: str, changelog_id: str) -> str:
    return f"https://bugcrowd.com/engagements/{source_id}/changelog/{changelog_id}.json"


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_scope_from_changelog(payload: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    scope_groups = data.get("scope", []) if isinstance(data, dict) else []
    if not isinstance(scope_groups, list):
        return {"in": [], "out": []}

    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []

    for group in scope_groups:
        if not isinstance(group, dict):
            continue
        in_scope = bool(group.get("inScope"))
        group_name = clean_text(group.get("name"))
        group_description = clean_text(group.get("description") or group.get("descriptionHtml"))
        reward_range = clean_text(group.get("rewardRange"))
        group_notes = " | ".join(value for value in [group_name, reward_range, group_description] if value)

        targets = group.get("targets", [])
        if not isinstance(targets, list):
            continue

        for target_item in targets:
            if not isinstance(target_item, dict):
                continue
            target_value = clean_text(target_item.get("uri")) or clean_text(target_item.get("name"))
            if not target_value:
                continue

            category = clean_text(target_item.get("category"))
            description = clean_text(target_item.get("description"))
            tags = target_item.get("tags") or []
            tag_names = []
            if isinstance(tags, list):
                tag_names = [clean_text(tag.get("name")) for tag in tags if isinstance(tag, dict)]
                tag_names = [tag_name for tag_name in tag_names if tag_name]

            scope_notes = " | ".join(
                value for value in [group_notes, description, ", ".join(tag_names)] if value
            )

            scope_item = build_scope_item(
                target_value,
                type_hint=category,
                asset_hint=" ".join(tag_names),
                notes=scope_notes,
            )
            if not scope_item:
                continue

            if in_scope:
                in_scope_items.append(scope_item)
            else:
                reason = clean_text(group_description) or "Out of scope on Bugcrowd."
                out_scope_items.append({"target": target_value, "reason": reason})

    return {
        "in": dedupe_scope_items(in_scope_items),
        "out": dedupe_out_scope_items(out_scope_items),
    }


def _fetch_scope(source_id: str) -> dict[str, list[dict[str, str]]] | None:
    try:
        changelog_payload = fetch_json(_changelog_json_url(source_id), timeout=35)
    except Exception:
        return None

    if not isinstance(changelog_payload, dict):
        return None

    changelogs = changelog_payload.get("changelogs", [])
    if not isinstance(changelogs, list) or not changelogs:
        return None

    preferred = None
    for item in changelogs:
        if not isinstance(item, dict):
            continue
        tags = clean_text(item.get("tags")).lower()
        if tags in {"targets", "brief"}:
            preferred = item
            break
    if preferred is None:
        first_item = changelogs[0]
        if isinstance(first_item, dict):
            preferred = first_item
    if not preferred:
        return None

    changelog_id = clean_text(preferred.get("id"))
    if not changelog_id:
        return None

    try:
        detail_payload = fetch_json(_detail_changelog_json_url(source_id, changelog_id), timeout=45)
    except Exception:
        return None

    parsed_scope = _parse_scope_from_changelog(detail_payload if isinstance(detail_payload, dict) else {})
    if parsed_scope["in"] or parsed_scope["out"]:
        return parsed_scope
    return None


def collect() -> list[RawProgram]:
    try:
        staged_records: list[dict[str, object]] = []
        seen_ids: set[str] = set()

        for category in CATEGORIES:
            first_page, total_pages = _fetch_page(category, page=1)
            all_engagements = list(first_page)
            for page in range(2, total_pages + 1):
                page_engagements, _ = _fetch_page(category, page=page)
                all_engagements.extend(page_engagements)

            for item in all_engagements:
                if not isinstance(item, dict):
                    continue

                brief_url = str(item.get("briefUrl", "")).strip()
                if not brief_url:
                    continue

                source_id = brief_url.strip("/").split("/")[-1]
                if not source_id or source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                reward_summary = item.get("rewardSummary", {}) or {}
                summary = str(reward_summary.get("summary", "")).strip()
                hint = str(reward_summary.get("hint", "")).strip()
                min_reward = str(reward_summary.get("minReward", "")).strip()
                max_reward = str(reward_summary.get("maxReward", "")).strip()
                reward_blob = " ".join(value for value in [summary, hint, min_reward, max_reward] if value)

                bounty_min, bounty_max, currency = parse_money_range(reward_blob)
                lowered_rewards = reward_blob.lower()
                if bounty_max > 0:
                    bounty_type = "cash"
                elif "point" in lowered_rewards:
                    bounty_type = "points"
                elif "swag" in lowered_rewards:
                    bounty_type = "swag"
                else:
                    bounty_type = "none"

                engagement_type = str(
                    (item.get("productEngagementType", {}) or {}).get("label", "")
                ).strip()
                service_level = str(item.get("serviceLevel", "")).strip()
                description = (
                    str(item.get("tagline", "")).strip()
                    or f"{engagement_type or 'Public'} program on Bugcrowd."
                )

                staged_records.append(
                    {
                        "source_id": source_id,
                        "name": str(item.get("name", source_id)),
                        "description": description,
                        "url": urljoin("https://bugcrowd.com", brief_url),
                        "bounty_type": bounty_type,
                        "bounty_min": bounty_min,
                        "bounty_max": bounty_max,
                        "currency": currency,
                        "metadata": {
                            "regions": ["global"],
                            "recent_scope_expansion": False,
                            "engagementCategory": category,
                            "engagementType": engagement_type or category,
                            "serviceLevel": service_level,
                            "scopeRank": _safe_int(item.get("scopeRank")),
                        },
                    }
                )

        scope_by_source: dict[str, dict[str, list[dict[str, str]]]] = {}
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_scope, str(record["source_id"])): str(record["source_id"])
                for record in staged_records
            }
            for future in as_completed(futures):
                source_id = futures[future]
                try:
                    scope = future.result()
                except Exception:
                    continue
                if scope:
                    scope_by_source[source_id] = scope

        records: list[RawProgram] = []
        for record in staged_records:
            source_id = str(record["source_id"])
            records.append(
                build_program(
                    platform="Bugcrowd",
                    source_id=source_id,
                    name=str(record["name"]),
                    description=str(record["description"]),
                    url=str(record["url"]),
                    bounty_type=str(record["bounty_type"]),
                    bounty_min=int(record["bounty_min"]),
                    bounty_max=int(record["bounty_max"]),
                    bounty_currency=str(record["currency"]),
                    created_at=None,
                    updated_at=None,
                    metadata=record["metadata"] if isinstance(record["metadata"], dict) else None,
                    scope=scope_by_source.get(source_id),
                )
            )

        if records:
            return records
    except Exception as exc:
        print(f"[collector:bugcrowd] live fetch failed, using seed fallback: {exc}")

    return load_seed_programs("bugcrowd.json", "Bugcrowd")

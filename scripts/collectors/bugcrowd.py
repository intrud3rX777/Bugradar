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
    extract_targets_from_text,
    fetch_json,
    load_seed_programs,
    parse_money_range,
)

ENGAGEMENTS_URL = "https://bugcrowd.com/engagements.json"
CATEGORIES = ("bug_bounty", "vdp")
SORT_BY = "promoted"
SORT_DIRECTION = "desc"
DETAIL_WORKERS = 8


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return bool(value)


def _extract_tag_names(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = clean_text(value)
        if not cleaned:
            return []
        # Supports values like "targets, brief"
        return [part.strip().lower() for part in cleaned.split(",") if part.strip()]

    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            names.extend(_extract_tag_names(item))
        return names
    if isinstance(value, dict):
        for key in ("name", "label", "slug", "id", "value"):
            candidate = clean_text(value.get(key))
            if candidate:
                names.append(candidate.lower())
        return names
    return []


def _extract_scope_groups(payload: dict[str, object]) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    candidate_containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidate_containers.append(data)

    for container in candidate_containers:
        for key in ("scope", "targetGroups", "target_groups", "targets", "target_groups_attributes"):
            groups = container.get(key)
            if isinstance(groups, list):
                return [group for group in groups if isinstance(group, dict)]
    return []


def _extract_targets(group: dict[str, object]) -> list[dict[str, object]]:
    for key in ("targets", "assets", "targetAssets", "entries", "scopeTargets"):
        raw_targets = group.get(key)
        if isinstance(raw_targets, list):
            return [target for target in raw_targets if isinstance(target, dict)]
    return []


def _scope_reason(group_description: str, target_item: dict[str, object]) -> str:
    return (
        clean_text(target_item.get("reason"))
        or clean_text(target_item.get("outOfScopeReason"))
        or clean_text(target_item.get("out_scope_reason"))
        or group_description
        or "Out of scope on Bugcrowd."
    )


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
    scope_groups = _extract_scope_groups(payload)
    if not scope_groups:
        return {"in": [], "out": []}

    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []

    for group in scope_groups:
        in_scope = _as_bool(
            group.get("inScope", group.get("in_scope", group.get("isInScope", group.get("is_in_scope"))))
        )
        group_name = clean_text(group.get("name"))
        group_description = clean_text(group.get("description") or group.get("descriptionHtml"))
        reward_range = clean_text(group.get("rewardRange"))
        group_notes = " | ".join(value for value in [group_name, reward_range, group_description] if value)

        targets = _extract_targets(group)
        if not targets and group_description:
            # Some payloads expose only descriptive text; try extracting host/path-like targets.
            inferred_targets = extract_targets_from_text(group_description)
            targets = [{"uri": inferred_target} for inferred_target in inferred_targets]
        if not targets:
            continue

        for target_item in targets:
            target_value = (
                clean_text(target_item.get("uri"))
                or clean_text(target_item.get("target"))
                or clean_text(target_item.get("name"))
                or clean_text(target_item.get("value"))
            )
            if not target_value:
                continue

            category = (
                clean_text(target_item.get("category"))
                or clean_text(target_item.get("type"))
                or clean_text(target_item.get("targetType"))
                or clean_text(target_item.get("assetType"))
            )
            description = clean_text(target_item.get("description"))
            tag_names = _extract_tag_names(target_item.get("tags"))

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
                reason = _scope_reason(group_description, target_item)
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

    candidates: list[tuple[int, str]] = []
    for item in changelogs:
        if not isinstance(item, dict):
            continue
        changelog_id = clean_text(item.get("id"))
        if not changelog_id:
            continue
        tags = set(_extract_tag_names(item.get("tags")))
        title = clean_text(item.get("title")).lower()

        score = 0
        if tags & {"targets", "scope"}:
            score += 4
        if "target" in title or "scope" in title:
            score += 2
        if tags & {"brief", "policy"}:
            score += 1
        candidates.append((score, changelog_id))

    if not candidates:
        return None

    # Prefer entries that explicitly mention scope/targets, but keep fallback candidates.
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for _, changelog_id in candidates[:10]:
        if changelog_id in seen_ids:
            continue
        seen_ids.add(changelog_id)
        try:
            detail_payload = fetch_json(_detail_changelog_json_url(source_id, changelog_id), timeout=45)
        except Exception:
            continue

        parsed_scope = _parse_scope_from_changelog(detail_payload if isinstance(detail_payload, dict) else {})
        if parsed_scope["in"]:
            in_scope_items.extend(parsed_scope["in"])
        if parsed_scope["out"]:
            out_scope_items.extend(parsed_scope["out"])
        if in_scope_items and out_scope_items:
            break

    deduped_in = dedupe_scope_items(in_scope_items)
    deduped_out = dedupe_out_scope_items(out_scope_items)
    if deduped_in or deduped_out:
        return {"in": deduped_in, "out": deduped_out}
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

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re

from .base import (
    RawProgram,
    build_program,
    build_scope_item,
    clean_text,
    dedupe_out_scope_items,
    dedupe_scope_items,
    extract_targets_from_text,
    fetch_text,
    from_timestamp,
    load_seed_programs,
)

PAGE_URL_TEMPLATE = (
    "https://www.intigriti.com/researchers/bug-bounty-programs?programs_prod%5Bpage%5D={page}"
)
INITIAL_STATE_RE = re.compile(
    r'window\[Symbol\.for\("InstantSearchInitialResults"\)\]\s*=\s*(\{.*?\})</script>',
    re.IGNORECASE | re.DOTALL,
)
PROGRAM_STATE_RE = re.compile(
    r'<script[^>]*id="my-app-state"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
MARKDOWN_BULLET_RE = re.compile(r"(?m)^\s*[-*]\s+(.+)$")
DETAIL_WORKERS = 8

ASSET_TYPE_HINTS_BY_ID = {
    1: "domain",
    2: "url",
    3: "mobile",
    4: "api",
    5: "network",
    6: "other",
    7: "wildcard",
    8: "source code",
}


def _load_page(page: int) -> tuple[list[dict], int]:
    html = fetch_text(PAGE_URL_TEMPLATE.format(page=page), timeout=45)
    match = INITIAL_STATE_RE.search(html)
    if not match:
        raise ValueError("Intigriti initial state payload not found")

    payload = json.loads(match.group(1))
    result = payload["programs_prod"]["results"][0]
    hits = result.get("hits", [])
    total_pages = int(result.get("nbPages", 1))
    return hits, total_pages


def _extract_program_state(page_html: str) -> dict[str, object] | None:
    match = PROGRAM_STATE_RE.search(page_html)
    if not match:
        return None

    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        return None

    for value in payload.values():
        if not isinstance(value, dict):
            continue
        candidate = value.get("b")
        if not isinstance(candidate, dict):
            continue
        if "assetsAndGroups" in candidate and "handle" in candidate:
            return candidate
    return None


def _extract_markdown_bullets(value: str | None) -> list[str]:
    text = value or ""
    return [clean_text(match.group(1)) for match in MARKDOWN_BULLET_RE.finditer(text) if clean_text(match.group(1))]


def _build_scope_from_program_state(program_state: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []

    assets_and_groups = program_state.get("assetsAndGroups", [])
    if isinstance(assets_and_groups, list):
        for group in assets_and_groups:
            if not isinstance(group, dict):
                continue
            content = group.get("content", [])
            if not isinstance(content, list):
                continue
            for asset in content:
                if not isinstance(asset, dict):
                    continue
                name = clean_text(asset.get("name"))
                if not name:
                    continue
                type_id = asset.get("typeId")
                try:
                    type_hint = ASSET_TYPE_HINTS_BY_ID.get(int(type_id), "other")
                except (TypeError, ValueError):
                    type_hint = "other"
                description = clean_text(asset.get("description"))
                scope_item = build_scope_item(
                    name,
                    type_hint=type_hint,
                    asset_hint=description,
                    notes=description,
                )
                if scope_item:
                    in_scope_items.append(scope_item)

    in_scope_notes = program_state.get("inScopes", [])
    if isinstance(in_scope_notes, list):
        for entry in in_scope_notes:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content", {})
            if not isinstance(content, dict):
                continue
            markdown = str(content.get("content", ""))
            for target in extract_targets_from_text(markdown):
                scope_item = build_scope_item(
                    target,
                    type_hint=None,
                    asset_hint="intigriti in-scope guidance",
                    notes="Intigriti in-scope guidance",
                )
                if scope_item:
                    in_scope_items.append(scope_item)

    out_scope_notes = program_state.get("outOfScopes", [])
    if isinstance(out_scope_notes, list):
        for entry in out_scope_notes:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content", {})
            if not isinstance(content, dict):
                continue
            markdown = str(content.get("content", ""))
            targets = extract_targets_from_text(markdown)
            reason = "Out of scope on Intigriti."
            for bullet in _extract_markdown_bullets(markdown):
                reason = bullet
                break

            if targets:
                for target in targets[:20]:
                    out_scope_items.append({"target": target, "reason": reason})
            else:
                bullets = _extract_markdown_bullets(markdown)
                if bullets:
                    for bullet in bullets[:20]:
                        out_scope_items.append({"target": bullet, "reason": "Out of scope on Intigriti."})

    return {
        "in": dedupe_scope_items(in_scope_items),
        "out": dedupe_out_scope_items(out_scope_items),
    }


def _fetch_scope(program_url: str) -> dict[str, list[dict[str, str]]] | None:
    if not program_url.startswith("https://app.intigriti.com/programs/"):
        return None
    try:
        page_html = fetch_text(program_url, timeout=45)
        program_state = _extract_program_state(page_html)
        if not program_state:
            return None
        scope = _build_scope_from_program_state(program_state)
        if scope["in"] or scope["out"]:
            return scope
    except Exception:
        return None
    return None


def collect() -> list[RawProgram]:
    try:
        staged_records: list[dict[str, object]] = []
        seen_ids: set[str] = set()

        first_hits, total_pages = _load_page(1)
        all_hits = list(first_hits)
        for page in range(2, total_pages + 1):
            page_hits, _ = _load_page(page)
            all_hits.extend(page_hits)

        for hit in all_hits:
            source_id = str(hit.get("handle") or hit.get("programId") or "").strip()
            if not source_id or source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            min_bounty = int(hit.get("minBounty", {}).get("value", 0) or 0)
            max_bounty = int(hit.get("maxBounty", {}).get("value", 0) or 0)
            bounty_type = "cash" if max_bounty > 0 else "none"
            currency = str(hit.get("maxBounty", {}).get("currency", "USD") or "USD")
            last_submission_raw = hit.get("lastSubmissionAt")
            last_submission_at = (
                from_timestamp(last_submission_raw) if last_submission_raw else None
            )

            company_handle = str(hit.get("companyHandle", "")).strip()
            handle = str(hit.get("handle", "")).strip()
            target_url = (
                f"https://app.intigriti.com/programs/{company_handle}/{handle}"
                if company_handle and handle
                else f"https://www.intigriti.com/researchers/bug-bounty-programs?program={source_id}"
            )

            staged_records.append(
                {
                    "source_id": source_id,
                    "name": str(hit.get("name", source_id)),
                    "description": clean_text(str(hit.get("description", ""))),
                    "url": target_url,
                    "bounty_type": bounty_type,
                    "min_bounty": min_bounty,
                    "max_bounty": max_bounty,
                    "currency": currency,
                    "created_at": from_timestamp(hit.get("createdAt")),
                    "updated_at": from_timestamp(hit.get("lastUpdatedAt")),
                    "metadata": {
                        "regions": ["global"],
                        "recent_scope_expansion": False,
                        "last_submission_at": last_submission_at,
                    },
                }
            )

        scope_by_source: dict[str, dict[str, list[dict[str, str]]]] = {}
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_scope, str(record["url"])): str(record["source_id"])
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
                    platform="Intigriti",
                    source_id=source_id,
                    name=str(record["name"]),
                    description=str(record["description"]),
                    url=str(record["url"]),
                    bounty_type=str(record["bounty_type"]),
                    bounty_min=int(record["min_bounty"]),
                    bounty_max=int(record["max_bounty"]),
                    bounty_currency=str(record["currency"]),
                    created_at=str(record["created_at"]),
                    updated_at=str(record["updated_at"]),
                    metadata=record["metadata"] if isinstance(record["metadata"], dict) else None,
                    scope=scope_by_source.get(source_id),
                )
            )

        if records:
            return records
    except Exception as exc:
        print(f"[collector:intigriti] live fetch failed, using seed fallback: {exc}")

    return load_seed_programs("intigriti.json", "Intigriti")

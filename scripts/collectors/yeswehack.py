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
    fetch_text,
    from_date,
    load_seed_programs,
    parse_money_range,
)

PROGRAMS_URL_TEMPLATE = "https://yeswehack.com/programs?page={page}"
CARD_SPLIT_RE = re.compile(r"<ywh-program-card\b", re.IGNORECASE)
CARD_ANCHOR_RE = re.compile(
    r'id="program-card-([^"]+?)-title-redirect-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
COMPANY_RE = re.compile(r'class="subtitle bu-name"[^>]*>(.*?)<!---->', re.IGNORECASE | re.DOTALL)
REWARDS_RE = re.compile(r'class="rewards"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)
UPDATED_RE = re.compile(r"Last update on</span><span[^>]*>([^<]+)</span>", re.IGNORECASE | re.DOTALL)
REPORTS_RE = re.compile(
    r">\s*Reports\s*</span>\s*<span[^>]*>\s*([0-9][0-9,]*)\s*</span>",
    re.IGNORECASE | re.DOTALL,
)
PAGE_RE = re.compile(r'id="pagination-page-(\d+)-link"', re.IGNORECASE)
STATE_RE = re.compile(
    r'<script[^>]*id="ng-state"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
DETAIL_WORKERS = 8

SCOPE_TYPE_HINTS = {
    "wildcard-domain": "wildcard",
    "web-application": "url",
    "website": "url",
    "api": "api",
    "mobile-application": "mobile",
    "mobile-app": "mobile",
    "open-source": "source code",
    "other": "other",
    "network": "network",
    "host": "domain",
}


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_cards(html: str) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    chunks = CARD_SPLIT_RE.split(html)
    for chunk in chunks:
        anchor_match = CARD_ANCHOR_RE.search(chunk)
        if not anchor_match:
            continue

        source_id, href, name_html = anchor_match.groups()
        full_url = href if href.startswith("http") else f"https://yeswehack.com{href}"
        name = clean_text(name_html)

        company_match = COMPANY_RE.search(chunk)
        company = clean_text(company_match.group(1) if company_match else "")

        rewards_match = REWARDS_RE.search(chunk)
        rewards_text = clean_text(rewards_match.group(1) if rewards_match else "")
        min_bounty, max_bounty, currency = parse_money_range(rewards_text)

        is_bug_bounty = "Bug bounty" in chunk
        bounty_type = "cash" if max_bounty > 0 else ("none" if is_bug_bounty else "none")

        updated_match = UPDATED_RE.search(chunk)
        updated_at = from_date(clean_text(updated_match.group(1)) if updated_match else "")

        reports_match = REPORTS_RE.search(chunk)
        submission_count = (
            int(reports_match.group(1).replace(",", "")) if reports_match else None
        )

        cards.append(
            {
                "source_id": source_id,
                "name": name,
                "description": (
                    f"{company} public program on YesWeHack."
                    if company
                    else "Public program on YesWeHack."
                ),
                "url": full_url,
                "bounty_type": bounty_type,
                "bounty_min": min_bounty,
                "bounty_max": max_bounty,
                "currency": currency,
                "updated_at": updated_at,
                "submission_count": submission_count,
            }
        )
    return cards


def _extract_program_payload(page_html: str) -> dict[str, object] | None:
    match = STATE_RE.search(page_html)
    if not match:
        return None
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        return None

    for key, value in payload.items():
        if not key.startswith("getProgram-"):
            continue
        if not isinstance(value, dict):
            continue
        data = value.get("data")
        if isinstance(data, dict):
            return data
    return None


def _build_scope(program_payload: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []

    scopes = program_payload.get("scopes", [])
    if isinstance(scopes, list):
        for scope_entry in scopes:
            if not isinstance(scope_entry, dict):
                continue
            target = clean_text(scope_entry.get("scope"))
            if not target:
                continue
            raw_type = clean_text(scope_entry.get("scope_type")).lower()
            type_hint = SCOPE_TYPE_HINTS.get(raw_type, raw_type or None)
            scope_type_name = clean_text(scope_entry.get("scope_type_name"))
            asset_value = clean_text(scope_entry.get("asset_value"))
            notes = " | ".join(value for value in [scope_type_name, asset_value] if value)
            scope_item = build_scope_item(
                target,
                type_hint=type_hint,
                asset_hint=f"{scope_type_name} {asset_value}",
                notes=notes,
            )
            if scope_item:
                in_scope_items.append(scope_item)

    out_values = program_payload.get("out_of_scope", [])
    if isinstance(out_values, list):
        for value in out_values:
            target = clean_text(value)
            if not target:
                continue
            out_scope_items.append({"target": target, "reason": "Out of scope on YesWeHack."})

    return {
        "in": dedupe_scope_items(in_scope_items),
        "out": dedupe_out_scope_items(out_scope_items),
    }


def _fetch_program_detail(program_url: str) -> dict[str, object] | None:
    try:
        page_html = fetch_text(program_url, timeout=45)
        payload = _extract_program_payload(page_html)
        if not payload:
            return None
        scope = _build_scope(payload)
        details: dict[str, object] = {}
        if scope["in"] or scope["out"]:
            details["scope"] = scope
        details["submission_count"] = _safe_int(payload.get("reports_count"))
        return details
    except Exception:
        return None


def collect() -> list[RawProgram]:
    try:
        first_html = fetch_text(PROGRAMS_URL_TEMPLATE.format(page=1), timeout=60)
        page_numbers = [int(page) for page in PAGE_RE.findall(first_html)] or [1]
        max_page = max([1, *page_numbers])

        staged_records: list[dict[str, object]] = []
        seen_ids: set[str] = set()

        for page in range(1, max_page + 1):
            html = first_html if page == 1 else fetch_text(PROGRAMS_URL_TEMPLATE.format(page=page), timeout=60)
            for program in _parse_cards(html):
                source_id = str(program.get("source_id", ""))
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)
                staged_records.append(program)

        detail_by_source: dict[str, dict[str, object]] = {}
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_program_detail, str(record["url"])): str(record["source_id"])
                for record in staged_records
            }
            for future in as_completed(futures):
                source_id = futures[future]
                try:
                    detail = future.result()
                except Exception:
                    continue
                if detail:
                    detail_by_source[source_id] = detail

        records: list[RawProgram] = []
        for record in staged_records:
            source_id = str(record["source_id"])
            detail = detail_by_source.get(source_id, {})
            submission_count = detail.get("submission_count")
            if not isinstance(submission_count, int):
                submission_count = (
                    int(record["submission_count"])
                    if isinstance(record.get("submission_count"), int)
                    else None
                )

            records.append(
                build_program(
                    platform="YesWeHack",
                    source_id=source_id,
                    name=str(record["name"]),
                    description=str(record["description"]),
                    url=str(record["url"]),
                    bounty_type=str(record["bounty_type"]),
                    bounty_min=int(record["bounty_min"]),
                    bounty_max=int(record["bounty_max"]),
                    bounty_currency=str(record["currency"]),
                    created_at=str(record["updated_at"]),
                    updated_at=str(record["updated_at"]),
                    metadata={
                        "regions": ["global"],
                        "recent_scope_expansion": False,
                        "submission_count": submission_count,
                    },
                    scope=detail.get("scope") if isinstance(detail.get("scope"), dict) else None,
                )
            )

        if records:
            return records
    except Exception as exc:
        print(f"[collector:yeswehack] live fetch failed, using seed fallback: {exc}")

    return load_seed_programs("yeswehack.json", "YesWeHack")

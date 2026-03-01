from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from urllib.request import Request, urlopen

from .base import (
    DEFAULT_HEADERS,
    RawProgram,
    build_program,
    build_scope_item,
    clean_text,
    dedupe_out_scope_items,
    dedupe_scope_items,
    load_seed_programs,
    parse_money_range,
    fetch_text,
)

GRAPHQL_URL = "https://hackerone.com/graphql"
PAGE_SIZE = 100
SCOPE_PAGE_SIZE = 200
SCOPE_WORKERS = 8
OPPORTUNITIES_QUERY = """
query OpportunityCollector($query: OpportunitiesQuery!, $filter: QueryInput!, $from: Int, $size: Int, $sort: [SortInput!]) {
  opportunities_search(query: $query, filter: $filter, from: $from, size: $size, sort: $sort) {
    total_count
    nodes {
      __typename
      ... on OpportunityDocument {
        id
        handle
        name
        state
        profile_picture
        offers_bounties
        minimum_bounty_table_value
        maximum_bounty_table_value
        currency
        launched_at
        last_updated_at
        resolved_report_count
        awarded_report_count
        awarded_reporter_count
        submission_state
        team_type
      }
    }
  }
}
"""
STRUCTURED_SCOPE_QUERY = """
query PolicySearchStructuredScopesQuery($handle: String!, $from: Int, $size: Int, $sort: SortInput) {
  team(handle: $handle) {
    id
    structured_scopes_search(from: $from, size: $size, sort: $sort) {
      total_count
      nodes {
        ... on StructuredScopeDocument {
          id
          identifier
          display_name
          instruction
          cvss_score
          eligible_for_bounty
          eligible_for_submission
          asm_system_tags
          updated_at
          total_resolved_reports
        }
      }
    }
  }
}
"""

PROGRAMS_URL = "https://www.hackerone.com/bug-bounty-programs"
ANCHOR_RE = re.compile(
    r'<a href="(https://hackerone\.com/[^"]+)"[^>]*class="bug-bounty-list-item[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
NAME_RE = re.compile(r'bug-bounty-list-item-name[^>]*>(.*?)</h3>', re.IGNORECASE | re.DOTALL)
POLICY_RE = re.compile(r'bug-bounty-list-item-policy[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
BOUNTY_MARKER_RE = re.compile(r"\bOffers bounties\b", re.IGNORECASE)
MIN_BOUNTY_RE = re.compile(r'bug-bounty-list-item-meta-item min-bounty[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)

DISPLAY_SCOPE_TYPE_HINTS = {
    "wildcard": "wildcard",
    "domain": "domain",
    "url": "url",
    "api": "api",
    "sourcecode": "source code",
    "smartcontract": "source code",
    "androidplaystore": "mobile",
    "iosappstore": "mobile",
    "otherasset": "other",
}


def _safe_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _decode_opportunity_id(global_id: str) -> str:
    if not global_id:
        return ""
    try:
        decoded = base64.b64decode(global_id).decode("utf-8")
    except Exception:
        return ""
    if "/" not in decoded:
        return ""
    return decoded.rsplit("/", 1)[-1].strip()


def _graphql_request(query: str, variables: dict[str, object]) -> dict[str, object]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        **DEFAULT_HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(GRAPHQL_URL, data=payload, headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8", errors="ignore"))


def _fetch_scope_for_handle(handle: str) -> dict[str, list[dict[str, str]]] | None:
    if not handle:
        return None

    in_scope_items: list[dict[str, str]] = []
    out_scope_items: list[dict[str, str]] = []
    total_count: int | None = None
    offset = 0

    while True:
        payload = _graphql_request(
            STRUCTURED_SCOPE_QUERY,
            {
                "handle": handle,
                "from": offset,
                "size": SCOPE_PAGE_SIZE,
                "sort": {"field": "updated_at", "direction": "DESC"},
            },
        )
        team = payload.get("data", {}).get("team", {}) if isinstance(payload, dict) else {}
        if not isinstance(team, dict) or not team:
            break
        search = team.get("structured_scopes_search", {})
        if not isinstance(search, dict):
            break

        if total_count is None:
            total_count = _safe_int(search.get("total_count"))

        nodes = search.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            break

        for node in nodes:
            if not isinstance(node, dict):
                continue
            identifier = clean_text(node.get("identifier"))
            if not identifier:
                continue

            display_name = clean_text(node.get("display_name"))
            type_hint = DISPLAY_SCOPE_TYPE_HINTS.get(display_name.lower(), display_name.lower())
            instruction = clean_text(node.get("instruction"))
            asm_tags = node.get("asm_system_tags") if isinstance(node.get("asm_system_tags"), list) else []
            asm_tag_text = " ".join(clean_text(value) for value in asm_tags if clean_text(value))

            notes = " | ".join(value for value in [display_name, instruction] if value)
            scope_item = build_scope_item(
                identifier,
                type_hint=type_hint,
                asset_hint=asm_tag_text,
                notes=notes,
            )
            if not scope_item:
                continue

            eligible_for_submission = bool(node.get("eligible_for_submission"))
            if eligible_for_submission:
                in_scope_items.append(scope_item)
            else:
                out_scope_items.append(
                    {
                        "target": identifier,
                        "reason": instruction or "Not eligible for submission.",
                    }
                )

        offset += SCOPE_PAGE_SIZE
        if total_count is not None and offset >= total_count:
            break

    in_scope = dedupe_scope_items(in_scope_items)
    out_scope = dedupe_out_scope_items(out_scope_items)
    if in_scope or out_scope:
        return {"in": in_scope, "out": out_scope}
    return None


def _collect_from_opportunities() -> list[RawProgram]:
    staged_records: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    total_count: int | None = None
    offset = 0
    while True:
        payload = _graphql_request(
            OPPORTUNITIES_QUERY,
            {
                "query": {},
                "filter": {},
                "from": offset,
                "size": PAGE_SIZE,
                "sort": [{"field": "handle", "direction": "ASC"}],
            },
        )
        search = (
            payload.get("data", {}).get("opportunities_search", {})
            if isinstance(payload, dict)
            else {}
        )
        if not isinstance(search, dict):
            break

        if total_count is None:
            total_count = _safe_int(search.get("total_count"))

        nodes = search.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            break

        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("__typename", "")).strip() != "OpportunityDocument":
                continue

            handle = str(node.get("handle", "")).strip()
            raw_opportunity_id = str(node.get("id", "")).strip()
            decoded_opportunity_id = _decode_opportunity_id(raw_opportunity_id)
            unique_part = decoded_opportunity_id or raw_opportunity_id
            source_id = f"{handle}-{unique_part}" if handle and unique_part else (handle or unique_part)
            if not source_id or source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            name = str(node.get("name", handle or source_id)).strip()
            offers_bounties = bool(node.get("offers_bounties"))
            bounty_min = _safe_int(node.get("minimum_bounty_table_value"))
            bounty_max = _safe_int(node.get("maximum_bounty_table_value"))
            if offers_bounties and bounty_max <= 0 and bounty_min > 0:
                bounty_max = bounty_min
            bounty_type = "cash" if offers_bounties and bounty_max > 0 else "none"
            currency = str(node.get("currency", "USD") or "USD")

            team_type = str(node.get("team_type", "")).strip().replace("_", " ").lower()
            submission_state = str(node.get("submission_state", "")).strip().replace("_", " ").lower()
            description = "Public opportunity on HackerOne."
            if team_type and submission_state:
                description = f"{team_type.title()} {submission_state} opportunity on HackerOne."
            elif team_type:
                description = f"{team_type.title()} opportunity on HackerOne."

            resolved_report_count = _safe_int(node.get("resolved_report_count"))
            awarded_report_count = _safe_int(node.get("awarded_report_count"))
            awarded_reporter_count = _safe_int(node.get("awarded_reporter_count"))

            staged_records.append(
                {
                    "source_id": source_id,
                    "name": name,
                    "description": description,
                    "url": f"https://hackerone.com/{handle}" if handle else "https://hackerone.com/opportunities/all",
                    "bounty_type": bounty_type,
                    "bounty_min": bounty_min,
                    "bounty_max": bounty_max,
                    "currency": currency,
                    "created_at": str(node.get("launched_at") or "").strip() or None,
                    "updated_at": str(node.get("last_updated_at") or "").strip() or None,
                    "handle": handle or None,
                    "metadata": {
                        "regions": ["global"],
                        "recent_scope_expansion": False,
                        "submission_count": resolved_report_count or None,
                        "awarded_report_count": awarded_report_count or None,
                        "awarded_reporter_count": awarded_reporter_count or None,
                        "opportunity_state": str(node.get("state", "")).strip(),
                        "submission_state": str(node.get("submission_state", "")).strip(),
                        "team_type": str(node.get("team_type", "")).strip(),
                        "opportunity_handle": handle or None,
                        "opportunity_id": raw_opportunity_id or None,
                        "opportunity_numeric_id": decoded_opportunity_id or None,
                    },
                }
            )

        offset += PAGE_SIZE
        if total_count is not None and offset >= total_count:
            break

    handles = sorted(
        {
            str(record["handle"])
            for record in staged_records
            if isinstance(record.get("handle"), str) and str(record.get("handle")).strip()
        }
    )
    scope_by_handle: dict[str, dict[str, list[dict[str, str]]]] = {}
    with ThreadPoolExecutor(max_workers=SCOPE_WORKERS) as executor:
        futures = {executor.submit(_fetch_scope_for_handle, handle): handle for handle in handles}
        for future in as_completed(futures):
            handle = futures[future]
            try:
                scope = future.result()
            except Exception:
                continue
            if scope:
                scope_by_handle[handle] = scope

    records: list[RawProgram] = []
    for record in staged_records:
        handle = str(record.get("handle") or "").strip()
        records.append(
            build_program(
                platform="HackerOne",
                source_id=str(record["source_id"]),
                name=str(record["name"]),
                description=str(record["description"]),
                url=str(record["url"]),
                bounty_type=str(record["bounty_type"]),
                bounty_min=int(record["bounty_min"]),
                bounty_max=int(record["bounty_max"]),
                bounty_currency=str(record["currency"]),
                created_at=record.get("created_at") if isinstance(record.get("created_at"), str) else None,
                updated_at=record.get("updated_at") if isinstance(record.get("updated_at"), str) else None,
                metadata=record["metadata"] if isinstance(record["metadata"], dict) else None,
                scope=scope_by_handle.get(handle),
            )
        )

    return records


def _collect_from_legacy_listing() -> list[RawProgram]:
    html = fetch_text(PROGRAMS_URL, timeout=45)
    staged_records: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for href, block in ANCHOR_RE.findall(html):
        source_id = href.rstrip("/").rsplit("/", 1)[-1]
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        raw_name = NAME_RE.search(block)
        name = clean_text(raw_name.group(1) if raw_name else source_id)
        raw_description = POLICY_RE.search(block)
        description = clean_text(raw_description.group(1) if raw_description else "")

        bounty_type = "cash" if BOUNTY_MARKER_RE.search(block) else "none"
        bounty_min = 0
        bounty_max = 0
        currency = "USD"

        min_bounty_match = MIN_BOUNTY_RE.search(block)
        if min_bounty_match:
            _, parsed_max, currency = parse_money_range(min_bounty_match.group(1))
            if parsed_max > 0:
                bounty_min = parsed_max
                bounty_max = max(parsed_max * 10, parsed_max)
                bounty_type = "cash"

        staged_records.append(
            {
                "source_id": source_id,
                "name": name,
                "description": description,
                "url": href,
                "bounty_type": bounty_type,
                "bounty_min": bounty_min,
                "bounty_max": bounty_max,
                "currency": currency,
                "handle": source_id,
            }
        )

    scope_by_handle: dict[str, dict[str, list[dict[str, str]]]] = {}
    with ThreadPoolExecutor(max_workers=SCOPE_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_scope_for_handle, str(record["handle"])): str(record["handle"])
            for record in staged_records
        }
        for future in as_completed(futures):
            handle = futures[future]
            try:
                scope = future.result()
            except Exception:
                continue
            if scope:
                scope_by_handle[handle] = scope

    records: list[RawProgram] = []
    for record in staged_records:
        handle = str(record["handle"])
        records.append(
            build_program(
                platform="HackerOne",
                source_id=handle,
                name=str(record["name"]),
                description=str(record["description"]),
                url=str(record["url"]),
                bounty_type=str(record["bounty_type"]),
                bounty_min=int(record["bounty_min"]),
                bounty_max=int(record["bounty_max"]),
                bounty_currency=str(record["currency"]),
                metadata={"regions": ["global"], "recent_scope_expansion": False},
                scope=scope_by_handle.get(handle),
            )
        )

    return records


def collect() -> list[RawProgram]:
    try:
        records = _collect_from_opportunities()
        if records:
            return records
    except Exception as exc:
        print(f"[collector:hackerone] opportunities fetch failed, trying legacy listing: {exc}")

    try:
        records = _collect_from_legacy_listing()
        if records:
            return records
    except Exception as exc:
        print(f"[collector:hackerone] legacy listing fetch failed, using seed fallback: {exc}")

    return load_seed_programs("hackerone.json", "HackerOne")

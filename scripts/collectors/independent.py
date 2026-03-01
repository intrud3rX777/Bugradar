from __future__ import annotations

import os
import re
from urllib.parse import urljoin

from .base import RawProgram, build_program, clean_text, fetch_text, load_seed_programs

LIST_URL_TEMPLATE = "https://firebounty.com/?page={page}"
ROW_MARKER = '<div class="Rtable-row"'
TOTAL_PAGES_RE = re.compile(r'href="\?page=(\d+)"', re.IGNORECASE)
DATA_URL_RE = re.compile(r'data-url="([^"]+)"', re.IGNORECASE)
NAME_CONTENT_RE = re.compile(
    r'<div class="Rtable-cell--content name-content">(.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
TYPE_CONTENT_RE = re.compile(
    r'<div class="Rtable-cell--content type-content">(.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
CREATED_CONTENT_RE = re.compile(
    r'<div class="Rtable-cell--content created-content">(.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
UPDATED_CONTENT_RE = re.compile(
    r'<div class="Rtable-cell--content updated-content">(.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
REWARD_CONTENT_RE = re.compile(
    r'<div class="Rtable-cell--content reward-content">(.*?)</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
NAME_ANCHOR_RE = re.compile(r"<a[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
BADGE_RE = re.compile(r"<span[^>]*class='badge[^']*'[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b",
    re.IGNORECASE,
)

MAX_PAGES = int(os.getenv("BUG_RADAR_INDEPENDENT_MAX_PAGES", "6"))
MAX_PROGRAMS = int(os.getenv("BUG_RADAR_INDEPENDENT_MAX_PROGRAMS", "600"))


def _total_pages(html: str) -> int:
    pages = [int(page) for page in TOTAL_PAGES_RE.findall(html)]
    return max([1, *pages])


def _rows(html: str) -> list[str]:
    return [f"{ROW_MARKER}{chunk}" for chunk in html.split(ROW_MARKER)[1:]]


def _first_domain(text: str) -> str | None:
    match = DOMAIN_RE.search(text)
    if not match:
        return None
    return match.group(0).lower()


def _content(row: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(row)
    return clean_text(match.group(1)) if match else ""


def _program_kind(type_text: str) -> str:
    lowered = type_text.lower()
    if "bug bounty" in lowered or "bbp" in lowered:
        return "BBP"
    if "security.txt" in lowered or "vdp" in lowered or "disclosure" in lowered:
        return "VDP"
    return "Unknown"


def _parse_row(row: str) -> RawProgram | None:
    data_url_match = DATA_URL_RE.search(row)
    if not data_url_match:
        return None
    detail_path = str(data_url_match.group(1)).strip()
    if not detail_path:
        return None

    name_block = _content(row, NAME_CONTENT_RE)
    type_text = _content(row, TYPE_CONTENT_RE)
    created_text = _content(row, CREATED_CONTENT_RE)
    updated_text = _content(row, UPDATED_CONTENT_RE)
    reward_text = _content(row, REWARD_CONTENT_RE)
    reward_badges = [clean_text(badge) for badge in BADGE_RE.findall(row)]
    reward_badges = [badge for badge in reward_badges if badge]

    anchor_match = NAME_ANCHOR_RE.search(row)
    display_name = clean_text(anchor_match.group(1)) if anchor_match else ""
    name = display_name or (name_block.split(" ", 1)[0] if name_block else "") or detail_path.strip("/")

    description_bits = [bit for bit in [name_block, type_text, reward_text] if bit]
    description = " | ".join(description_bits) if description_bits else "Independent disclosure program listed on FireBounty."

    domain = _first_domain(display_name or name_block or "")
    if domain:
        if "security.txt" in type_text.lower():
            program_url = f"https://{domain}/.well-known/security.txt"
        else:
            program_url = f"https://{domain}"
        scope = {
            "in": [
                {
                    "target": domain,
                    "type": "domain",
                    "asset_type": "web",
                    "notes": "Independent program domain from FireBounty listing.",
                }
            ],
            "out": [],
        }
    else:
        program_url = urljoin("https://firebounty.com", detail_path)
        scope = None

    kind = _program_kind(type_text)
    updated_value = updated_text if updated_text and updated_text != "-" else created_text

    return build_program(
        platform="Independent",
        source_id=detail_path.strip("/"),
        name=name,
        description=description,
        url=program_url,
        bounty_type="none",
        bounty_min=0,
        bounty_max=0,
        created_at=created_text or None,
        updated_at=updated_value or None,
        metadata={
            "regions": ["global"],
            "recent_scope_expansion": False,
            "source": "firebounty",
            "hosting": "independent",
            "program_kind": kind,
            "listing_type": type_text or None,
            "reward_badges": reward_badges,
        },
        scope=scope,
    )


def collect() -> list[RawProgram]:
    try:
        first_page_html = fetch_text(LIST_URL_TEMPLATE.format(page=1), timeout=60)
        page_count = min(_total_pages(first_page_html), max(1, MAX_PAGES))

        records: list[RawProgram] = []
        seen_ids: set[str] = set()

        for page in range(1, page_count + 1):
            html = first_page_html if page == 1 else fetch_text(LIST_URL_TEMPLATE.format(page=page), timeout=60)
            for row in _rows(html):
                parsed = _parse_row(row)
                if not parsed:
                    continue
                source_id = str(parsed.get("source_id", "")).strip()
                if not source_id or source_id in seen_ids:
                    continue
                seen_ids.add(source_id)
                records.append(parsed)
                if len(records) >= MAX_PROGRAMS:
                    return records

        if records:
            return records
    except Exception as exc:
        print(f"[collector:independent] live fetch failed, using seed fallback: {exc}")

    return load_seed_programs("independent.json", "Independent")

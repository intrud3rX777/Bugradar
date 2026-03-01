from __future__ import annotations

import re

from .base import RawProgram, build_program, clean_text, fetch_text, load_seed_programs

LIST_URL = "https://www.openbugbounty.org/bugbounty-list/"
PROGRAM_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(/[^"]+)"[^>]*>([^<][^<]{2,200})</a>',
    re.IGNORECASE | re.DOTALL,
)


def collect() -> list[RawProgram]:
    try:
        html = fetch_text(LIST_URL, timeout=40)
        records: list[RawProgram] = []
        seen_ids: set[str] = set()

        for href, name_raw in PROGRAM_ANCHOR_RE.findall(html):
            name = clean_text(name_raw)
            if not name:
                continue
            source_id = href.strip("/").replace("/", "-")
            if not source_id or source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            records.append(
                build_program(
                    platform="OpenBugBounty",
                    source_id=source_id,
                    name=name,
                    description="Public listing sourced from OpenBugBounty.",
                    url=f"https://www.openbugbounty.org{href}",
                    bounty_type="none",
                    bounty_min=0,
                    bounty_max=0,
                    metadata={"regions": ["global"], "recent_scope_expansion": False},
                )
            )

        if records:
            return records
    except Exception as exc:
        print(f"[collector:openbugbounty] live fetch failed, using seed fallback: {exc}")

    return load_seed_programs("openbugbounty.json", "OpenBugBounty")

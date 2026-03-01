from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

PROGRAMS_FILE = DATA_DIR / "programs.json"
HACKTIVITY_FILE = DATA_DIR / "hacktivity.json"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

HACKTIVITY_SORT = {"field": "latest_disclosable_activity_at", "direction": "DESC"}

HACKERONE_GRAPHQL_URL = "https://hackerone.com/graphql"
HACKERONE_PAGE_SIZE = 100
HACKERONE_MAX_ITEMS = 700
HACKERONE_QUERY = """
query HacktivitySearchQuery($queryString: String!, $from: Int, $size: Int, $sort: SortInput!) {
  me {
    id
  }
  search(
    index: CompleteHacktivityReportIndex
    query_string: $queryString
    from: $from
    size: $size
    sort: $sort
  ) {
    __typename
    total_count
    nodes {
      __typename
      ... on HacktivityDocument {
        id
        _id
        reporter {
          id
          username
          name
        }
        cve_ids
        cwe
        severity_rating
        upvoted: upvoted_by_current_user
        public
        report {
          id
          databaseId: _id
          title
          substate
          url
          disclosed_at
          report_generated_content {
            id
            hacktivity_summary
          }
        }
        votes
        team {
          id
          handle
          name
          medium_profile_picture: profile_picture(size: medium)
          url
          currency
        }
        total_awarded_amount
        latest_disclosable_action
        latest_disclosable_activity_at
        submitted_at
        disclosed
        has_collaboration
        collaborators {
          id
          username
          name
        }
      }
    }
  }
}
"""

BUGCROWD_CROWDSTREAM_URL = "https://bugcrowd.com/crowdstream.json"
BUGCROWD_MAX_ITEMS = 600

SIGNAL_MAX_PER_PLATFORM = 120
MAX_TOTAL_ITEMS = 1600
EXCLUDED_PLATFORMS = {"Independent"}

MONEY_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def normalize_iso(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            except ValueError:
                continue
    return None


def parse_money_usd(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = MONEY_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        parsed = float(raw)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return int(round(parsed))


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


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


def fetch_json(url: str, timeout: int = 45) -> Dict[str, Any]:
    request = Request(url, headers=DEFAULT_HEADERS)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="ignore")
            return json.loads(payload)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}") from exc


def post_graphql(query: str, variables: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        HACKERONE_GRAPHQL_URL,
        data=body,
        headers={**DEFAULT_HEADERS, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="ignore")
            return json.loads(payload)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {HACKERONE_GRAPHQL_URL}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error from {HACKERONE_GRAPHQL_URL}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {HACKERONE_GRAPHQL_URL}") from exc


def extract_program_key(platform: str, program: Dict[str, Any]) -> str:
    source_id = str(program.get("sourceId", "")).strip()
    url = str(program.get("url", "")).strip()

    if platform == "HackerOne":
        if "-" in source_id:
            return source_id.split("-", 1)[0].lower()
        handle = urlparse(url).path.strip("/").split("/")
        return (handle[0] if handle and handle[0] else "").lower()

    if platform == "Bugcrowd":
        path_parts = [segment for segment in urlparse(url).path.split("/") if segment]
        if "engagements" in path_parts:
            idx = path_parts.index("engagements")
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1].lower()
        return source_id.lower()

    return source_id.lower()


def build_program_lookup(programs: list[Dict[str, Any]]) -> dict[str, dict[str, Dict[str, str]]]:
    lookup: dict[str, dict[str, Dict[str, str]]] = {}
    for program in programs:
        if not isinstance(program, dict):
            continue
        program_id = str(program.get("id", "")).strip()
        platform = str(program.get("platform", "")).strip()
        if not program_id or not platform:
            continue
        key = extract_program_key(platform, program)
        if not key:
            continue
        lookup.setdefault(platform, {})[key] = {
            "id": program_id,
            "name": str(program.get("name", "Unknown Program")).strip() or "Unknown Program",
            "url": str(program.get("url", "")).strip(),
        }
    return lookup


def collect_hackerone_items(program_lookup: dict[str, dict[str, Dict[str, str]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while len(items) < HACKERONE_MAX_ITEMS:
        page_size = min(HACKERONE_PAGE_SIZE, HACKERONE_MAX_ITEMS - len(items))
        payload = post_graphql(
            HACKERONE_QUERY,
            {
                "queryString": "disclosed:true",
                "from": offset,
                "size": page_size,
                "sort": HACKTIVITY_SORT,
            },
        )

        search = payload.get("data", {}).get("search", {}) if isinstance(payload, dict) else {}
        nodes = search.get("nodes", []) if isinstance(search, dict) else []
        if not isinstance(nodes, list) or not nodes:
            break

        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("__typename", "")).strip() != "HacktivityDocument":
                continue

            team = node.get("team", {}) if isinstance(node.get("team"), dict) else {}
            report = node.get("report", {}) if isinstance(node.get("report"), dict) else {}
            generated_content = (
                report.get("report_generated_content", {})
                if isinstance(report.get("report_generated_content"), dict)
                else {}
            )
            reporter = node.get("reporter", {}) if isinstance(node.get("reporter"), dict) else {}

            handle = str(team.get("handle", "")).strip().lower()
            program_ref = program_lookup.get("HackerOne", {}).get(handle)

            report_url = str(report.get("url", "")).strip()
            if report_url.startswith("/"):
                report_url = f"https://hackerone.com{report_url}"
            team_url = str(team.get("url", "")).strip()
            if team_url.startswith("/"):
                team_url = f"https://hackerone.com{team_url}"

            timestamp = (
                normalize_iso(node.get("latest_disclosable_activity_at"))
                or normalize_iso(report.get("disclosed_at"))
                or normalize_iso(node.get("submitted_at"))
                or now_iso()
            )

            bounty_amount = None
            raw_bounty = node.get("total_awarded_amount")
            if raw_bounty is not None:
                try:
                    bounty_amount = int(float(raw_bounty))
                except (TypeError, ValueError):
                    bounty_amount = None

            bounty_label = f"${bounty_amount:,}" if isinstance(bounty_amount, int) and bounty_amount > 0 else None

            summary = str(generated_content.get("hacktivity_summary", "")).strip()
            if not summary:
                summary = str(node.get("latest_disclosable_action", "")).strip()
            if not summary:
                summary = "Disclosed activity observed on HackerOne."

            report_title = str(report.get("title", "")).strip()
            if not report_title:
                report_title = str(node.get("latest_disclosable_action", "")).strip() or "Disclosed report"

            program_name = (
                (program_ref.get("name", "") if program_ref else "")
                or str(team.get("name", "")).strip()
                or handle
                or "Unknown Program"
            )
            program_url = (
                (program_ref.get("url", "") if program_ref else "")
                or team_url
                or "https://hackerone.com/hacktivity/overview"
            )

            item_id = str(node.get("_id") or node.get("id") or f"h1-{offset + len(items)}")
            severity = str(node.get("severity_rating", "")).strip() or None
            report_state = str(report.get("substate", "")).strip() or None
            reporter_username = str(reporter.get("username", "")).strip() or None

            items.append(
                {
                    "id": f"h1-{item_id}",
                    "platform": "HackerOne",
                    "source": "hackerone_hacktivity",
                    "timestamp": timestamp,
                    "programId": program_ref.get("id") if program_ref else None,
                    "programName": program_name,
                    "programUrl": program_url,
                    "reportTitle": report_title,
                    "summary": summary,
                    "severity": severity,
                    "target": None,
                    "bountyAmountUsd": bounty_amount if isinstance(bounty_amount, int) and bounty_amount > 0 else None,
                    "bountyLabel": bounty_label,
                    "reporter": reporter_username,
                    "state": report_state,
                    "disclosed": to_bool(node.get("disclosed")),
                    "link": report_url or program_url,
                }
            )

        offset += len(nodes)
        if len(nodes) < page_size:
            break

    return items


def collect_bugcrowd_items(program_lookup: dict[str, dict[str, Dict[str, str]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    total_pages = 1

    while len(items) < BUGCROWD_MAX_ITEMS and page <= total_pages:
        params = urlencode({"page": page})
        payload = fetch_json(f"{BUGCROWD_CROWDSTREAM_URL}?{params}", timeout=45)
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(results, list) or not results:
            break

        meta = payload.get("pagination_meta", {}) if isinstance(payload, dict) else {}
        if isinstance(meta, dict):
            try:
                total_pages = max(1, int(meta.get("total_pages", total_pages)))
            except (TypeError, ValueError):
                total_pages = max(1, total_pages)

        for row in results:
            if not isinstance(row, dict):
                continue
            if len(items) >= BUGCROWD_MAX_ITEMS:
                break

            engagement_code = str(row.get("engagement_code", "")).strip().lower()
            program_ref = program_lookup.get("Bugcrowd", {}).get(engagement_code)

            engagement_path = str(row.get("engagement_path", "")).strip()
            engagement_url = (
                f"https://bugcrowd.com{engagement_path}"
                if engagement_path.startswith("/")
                else str(program_ref.get("url", "") if program_ref else "").strip()
            )

            created_at = normalize_iso(row.get("created_at"))
            accepted_at = normalize_iso(row.get("accepted_at"))
            closed_at = normalize_iso(row.get("closed_at"))
            timestamp = created_at or accepted_at or closed_at or now_iso()

            submission_text = str(row.get("submission_state_text", "")).strip()
            report_title = submission_text or "Crowdstream activity"

            summary_parts = [
                submission_text,
                str(row.get("submission_state_date_text", "")).strip(),
            ]
            summary = " ".join(part for part in summary_parts if part).strip() or "Accepted/disclosed activity on Bugcrowd."

            priority = row.get("priority")
            severity = f"P{priority}" if isinstance(priority, int) and priority > 0 else None

            raw_amount = str(row.get("amount", "")).strip()
            points = row.get("points")
            bounty_amount = parse_money_usd(raw_amount)
            if raw_amount:
                bounty_label = raw_amount
            elif isinstance(points, int) and points > 0:
                bounty_label = f"{points} points"
            else:
                bounty_label = None

            if program_ref:
                program_name = program_ref.get("name", "Unknown Program")
                program_url = program_ref.get("url", "") or engagement_url
            else:
                program_name = str(row.get("engagement_name", "")).strip() or engagement_code or "Unknown Program"
                program_url = engagement_url

            crowdstream_id = str(row.get("id", "")).strip() or f"{engagement_code}-{page}-{len(items)}"

            items.append(
                {
                    "id": f"bc-{crowdstream_id}",
                    "platform": "Bugcrowd",
                    "source": "bugcrowd_crowdstream",
                    "timestamp": timestamp,
                    "programId": program_ref.get("id") if program_ref else None,
                    "programName": program_name,
                    "programUrl": program_url,
                    "reportTitle": report_title,
                    "summary": summary,
                    "severity": severity,
                    "target": str(row.get("target", "")).strip() or None,
                    "bountyAmountUsd": bounty_amount,
                    "bountyLabel": bounty_label,
                    "reporter": None,
                    "state": str(row.get("substate", "")).strip() or None,
                    "disclosed": to_bool(row.get("disclosed")),
                    "link": program_url or "https://bugcrowd.com/crowdstream",
                }
            )

        page += 1

    return items


def build_platform_signal_items(programs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored_programs: list[dict[str, Any]] = []
    for program in programs:
        if not isinstance(program, dict):
            continue
        platform = str(program.get("platform", "")).strip()
        if platform in {"HackerOne", "Bugcrowd", *EXCLUDED_PLATFORMS}:
            continue
        timestamp = (
            normalize_iso(program.get("lastSubmissionAt"))
            or normalize_iso(program.get("lastUpdated"))
            or normalize_iso(program.get("createdAt"))
        )
        if not timestamp:
            continue
        scored_programs.append({**program, "_signalTimestamp": timestamp})

    scored_programs.sort(
        key=lambda item: parse_iso(str(item.get("_signalTimestamp", ""))),
        reverse=True,
    )

    counts: Counter[str] = Counter()
    items: list[dict[str, Any]] = []
    for program in scored_programs:
        platform = str(program.get("platform", "")).strip()
        if counts[platform] >= SIGNAL_MAX_PER_PLATFORM:
            continue
        counts[platform] += 1

        program_id = str(program.get("id", "")).strip()
        program_name = str(program.get("name", "Unknown Program")).strip() or "Unknown Program"
        program_url = str(program.get("url", "")).strip()
        submission_count = program.get("submissionCount")
        submissions_last_7d = program.get("submissionsLast7d")
        signal_timestamp = str(program.get("_signalTimestamp", "")).strip() or now_iso()

        if isinstance(submissions_last_7d, int) and submissions_last_7d > 0:
            summary = f"{submissions_last_7d} submission signal(s) in the last 7 days."
        elif isinstance(submission_count, int) and submission_count > 0:
            summary = f"{submission_count} total submission signal(s) published for this program."
        else:
            summary = "Program metadata indicates recent platform activity."

        report_title = "Program activity signal"
        bounty_max = program.get("bountyMaxUsd")
        bounty_amount = bounty_max if isinstance(bounty_max, int) and bounty_max > 0 else None
        bounty_label = f"${bounty_amount:,}" if isinstance(bounty_amount, int) else None

        items.append(
            {
                "id": f"signal-{program_id}",
                "platform": platform,
                "source": "platform_signal",
                "timestamp": signal_timestamp,
                "programId": program_id or None,
                "programName": program_name,
                "programUrl": program_url,
                "reportTitle": report_title,
                "summary": summary,
                "severity": None,
                "target": None,
                "bountyAmountUsd": bounty_amount,
                "bountyLabel": bounty_label,
                "reporter": None,
                "state": None,
                "disclosed": None,
                "link": program_url,
            }
        )

    return items


def main() -> None:
    programs_payload = load_json(PROGRAMS_FILE)
    programs = programs_payload.get("programs", [])
    if not isinstance(programs, list):
        programs = []

    generated_at = str(programs_payload.get("generatedAt", now_iso()))
    program_lookup = build_program_lookup(programs)

    all_items: list[dict[str, Any]] = []

    try:
        all_items.extend(collect_hackerone_items(program_lookup))
    except Exception as exc:
        print(f"[hacktivity] HackerOne feed fetch failed: {exc}")

    try:
        all_items.extend(collect_bugcrowd_items(program_lookup))
    except Exception as exc:
        print(f"[hacktivity] Bugcrowd feed fetch failed: {exc}")

    all_items.extend(build_platform_signal_items(programs))

    deduped: dict[str, dict[str, Any]] = {}
    for item in all_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("platform", "")).strip() in EXCLUDED_PLATFORMS:
            continue
        key = str(item.get("id", "")).strip()
        if not key:
            continue
        deduped[key] = item

    ordered_items = sorted(
        deduped.values(),
        key=lambda item: parse_iso(str(item.get("timestamp", ""))),
        reverse=True,
    )
    limited_items = ordered_items[:MAX_TOTAL_ITEMS]

    by_platform = Counter(str(item.get("platform", "Unknown")) for item in limited_items)
    by_source = Counter(str(item.get("source", "unknown")) for item in limited_items)

    payload = {
        "generatedAt": generated_at,
        "totalItems": len(ordered_items),
        "items": limited_items,
        "byPlatform": [
            {"platform": platform, "count": count}
            for platform, count in sorted(by_platform.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "bySource": [
            {"source": source, "count": count}
            for source, count in sorted(by_source.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    }

    write_json(HACKTIVITY_FILE, payload)
    print(f"[hacktivity] wrote {HACKTIVITY_FILE} ({len(limited_items)} of {len(ordered_items)} items)")


if __name__ == "__main__":
    main()

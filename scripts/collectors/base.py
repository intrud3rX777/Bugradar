from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Dict, List

RawProgram = Dict[str, Any]

SEED_DIR = Path(__file__).resolve().parents[1] / "seeds"
DEFAULT_TIMEOUT = 30
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/plain,*/*",
}

SYMBOL_TO_CURRENCY = {
    "$": "USD",
    "\u20ac": "EUR",
    "\u00a3": "GBP",
}

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
MONEY_RE = re.compile(r"([\$\u20ac\u00a3])\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
WILDCARD_DOMAIN_RE = re.compile(r"\*\.[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
DOMAIN_RE = re.compile(r"(?<![@\w-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?![@\w-])", re.IGNORECASE)
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
CIDR_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}$")
PLATFORM_HOSTS = {
    "hackerone.com",
    "www.hackerone.com",
    "bugcrowd.com",
    "www.bugcrowd.com",
    "yeswehack.com",
    "www.yeswehack.com",
    "intigriti.com",
    "www.intigriti.com",
    "app.intigriti.com",
    "openbugbounty.org",
    "www.openbugbounty.org",
    "firebounty.com",
    "www.firebounty.com",
}

class CollectorError(Exception):
    """Raised when collector seed data is invalid."""


def load_seed_programs(seed_file: str, expected_platform: str) -> List[RawProgram]:
    seed_path = SEED_DIR / seed_file
    if not seed_path.exists():
        raise CollectorError(f"Missing seed file: {seed_path}")

    with seed_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    platform = payload.get("platform")
    programs = payload.get("programs", [])

    if platform != expected_platform:
        raise CollectorError(
            f"{seed_file} platform mismatch. Expected {expected_platform}, got {platform}"
        )

    if not isinstance(programs, list):
        raise CollectorError(f"{seed_file} has invalid 'programs' payload")

    normalized: List[RawProgram] = []
    for item in programs:
        record = dict(item)
        record["platform"] = expected_platform
        normalized.append(record)
    return normalized


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def from_timestamp(timestamp: int | float | None) -> str:
    if not timestamp:
        return now_iso()
    parsed = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def from_date(date_str: str | None) -> str:
    if not date_str:
        return now_iso()
    value = date_str.strip()
    if not value:
        return now_iso()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return now_iso()


def fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    request = Request(url, headers=DEFAULT_HEADERS)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except HTTPError as exc:
        raise CollectorError(f"HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise CollectorError(f"Network error for {url}: {exc.reason}") from exc


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    return json.loads(fetch_text(url, timeout=timeout))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)
    text = unescape(text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text


def clean_target(value: str | None) -> str:
    cleaned = clean_text(value)
    return cleaned.strip("`'\"()[]{}<>.,;")


def extract_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def parse_money_range(value: str | None) -> tuple[int, int, str]:
    if not value:
        return 0, 0, "USD"

    matches = MONEY_RE.findall(value)
    if not matches:
        return 0, 0, "USD"

    numeric_values: list[int] = []
    currency = "USD"
    for symbol, amount in matches:
        currency = SYMBOL_TO_CURRENCY.get(symbol, "USD")
        parsed = amount.replace(",", "")
        try:
            numeric_values.append(int(float(parsed)))
        except ValueError:
            continue

    if not numeric_values:
        return 0, 0, currency
    if len(numeric_values) == 1:
        return 0, numeric_values[0], currency
    return min(numeric_values), max(numeric_values), currency


def infer_asset_type(text_blob: str) -> str:
    lowered = text_blob.lower()
    if "api" in lowered:
        return "api"
    if "mobile" in lowered or "android" in lowered or "ios" in lowered:
        return "mobile"
    if "cloud" in lowered:
        return "cloud"
    if "iot" in lowered or "firmware" in lowered:
        return "iot"
    if "source" in lowered or "github" in lowered:
        return "source code"
    return "web"


def infer_scope_type(target: str, type_hint: str | None = None) -> str:
    lowered = target.strip().lower()
    hint = (type_hint or "").strip().lower()

    if hint in {"api", "web-api", "rest", "graphql"}:
        return "api"
    if hint in {"mobile", "android", "ios", "application"}:
        return "mobile"
    if hint in {"source", "source_code", "source code", "repository", "open-source"}:
        return "source"
    if hint in {"cloud"}:
        return "cloud"
    if hint in {"wildcard", "domain", "hostname", "subdomain"}:
        return "domain"
    if hint in {"url", "uri", "path", "web", "website", "web-application"}:
        return "url"

    if lowered.startswith("*."):
        return "domain"
    if CIDR_RE.match(lowered) or IP_RE.match(lowered):
        return "network"
    if SCHEME_RE.match(lowered):
        if "/api" in lowered or "api." in lowered:
            return "api"
        return "url"
    if lowered.startswith("/"):
        return "url"
    if DOMAIN_RE.match(lowered):
        if "/api" in lowered:
            return "api"
        if "/" in lowered:
            return "url"
        return "domain"
    if "oauth" in lowered or "auth" in lowered:
        return "auth"
    return "other"


def build_scope_item(
    target: str | None,
    *,
    type_hint: str | None = None,
    asset_hint: str | None = None,
    notes: str | None = None,
    auth_required: bool = False,
) -> dict[str, Any] | None:
    clean_target_value = clean_target(target)
    if not clean_target_value:
        return None

    scope_type = infer_scope_type(clean_target_value, type_hint)
    asset_blob = " ".join(
        value for value in [clean_target_value, type_hint or "", asset_hint or "", notes or ""] if value
    )
    asset_type = infer_asset_type(asset_blob)

    if scope_type == "api":
        asset_type = "api"
    elif scope_type == "mobile":
        asset_type = "mobile"
    elif scope_type == "source":
        asset_type = "source code"

    return {
        "target": clean_target_value,
        "type": scope_type,
        "asset_type": asset_type,
        "auth_required": bool(auth_required),
        "notes": clean_text(notes),
    }


def dedupe_scope_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, bool]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        target = clean_target(item.get("target"))
        if not target:
            continue
        scope_type = clean_text(item.get("type", "other")).lower() or "other"
        asset_type = clean_text(item.get("asset_type", "web")).lower() or "web"
        auth_required = bool(item.get("auth_required", False))
        notes = clean_text(item.get("notes"))

        key = (target.lower(), scope_type, asset_type, auth_required)
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "target": target,
                "type": scope_type,
                "asset_type": asset_type,
                "auth_required": auth_required,
                "notes": notes,
            }
        )
    return unique


def dedupe_out_scope_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        target = clean_target(item.get("target"))
        reason = clean_text(item.get("reason"))
        if not target:
            continue
        key = (target.lower(), reason.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append({"target": target, "reason": reason or "Out of scope"})
    return unique


def extract_targets_from_text(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []

    candidates: list[str] = []
    candidates.extend(URL_RE.findall(text))
    candidates.extend(WILDCARD_DOMAIN_RE.findall(text))
    candidates.extend(DOMAIN_RE.findall(text))

    seen: set[str] = set()
    normalized: list[str] = []
    for candidate in candidates:
        clean_candidate = clean_target(candidate)
        if not clean_candidate:
            continue
        key = clean_candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean_candidate)
    return normalized


def infer_hq_country(name: str, description: str, url: str) -> str:
    haystack = f"{name} {description} {url}".lower()
    if ".in" in haystack or " india" in haystack:
        return "IN"
    return "Unknown"


def build_scope(url: str, description: str) -> dict[str, Any]:
    host = extract_host(url)
    asset_type = infer_asset_type(f"{url} {description}")

    # Platform listing URLs are not target scope assets.
    if not host or host in PLATFORM_HOSTS:
        return {
            "in": [
                {
                    "target": "public-program-scope",
                    "type": "other",
                    "asset_type": "web" if asset_type == "api" else asset_type,
                    "notes": "Scope details are not available from the source listing.",
                }
            ],
            "out": [],
        }

    in_scope = [
        {
            "target": host,
            "type": "domain",
            "asset_type": "web" if asset_type == "api" else asset_type,
        }
    ]

    if asset_type == "api":
        in_scope.append(
            {
                "target": f"https://{host}/api",
                "type": "api",
                "asset_type": "api",
            }
        )

    return {"in": in_scope, "out": []}


def normalize_source_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "unknown-program"


def build_program(
    *,
    platform: str,
    source_id: str,
    name: str,
    description: str,
    url: str,
    bounty_type: str,
    bounty_min: int = 0,
    bounty_max: int = 0,
    bounty_currency: str = "USD",
    created_at: str | None = None,
    updated_at: str | None = None,
    hq_country: str | None = None,
    rules: list[str] | None = None,
    exclusions: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
) -> RawProgram:
    safe_name = clean_text(name) or "Unnamed program"
    safe_description = clean_text(description)
    safe_url = url.strip()
    inferred_country = hq_country or infer_hq_country(safe_name, safe_description, safe_url)

    return {
        "platform": platform,
        "source_id": normalize_source_id(source_id),
        "name": safe_name,
        "description": safe_description,
        "url": safe_url,
        "bounty": {
            "type": bounty_type,
            "min": max(0, int(bounty_min)),
            "max": max(0, int(bounty_max)),
            "currency": bounty_currency,
        },
        "scope": scope or build_scope(safe_url, safe_description),
        "hq_country": inferred_country,
        "metadata": metadata
        or {
            "regions": ["india"] if inferred_country == "IN" else ["global"],
            "recent_scope_expansion": False,
        },
        "rules": rules
        or [
            "Follow platform policy and responsible disclosure.",
            "Include reproducible technical details.",
        ],
        "exclusions": exclusions or ["Social engineering", "DoS"],
        "created_at": from_date(created_at),
        "updated_at": from_date(updated_at),
    }


#!/usr/bin/env python3
"""
Scrape RedGIFs top/trending niches/tags into a JSON file.

This uses the same general approach as redgifs_downloader.py:
- aiohttp
- temporary RedGIFs token
- RedGIFs API requests
- no Playwright
- no Chromium

Examples:
    python scrape_redgifs_niches.py
    python scrape_redgifs_niches.py --limit 100
    python scrape_redgifs_niches.py --output data/redgifs_niches.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout


BASE_API = "https://api.redgifs.com/v2"
TOKEN_URL = f"{BASE_API}/auth/temporary"
EXPLORE_NICHES_URL = "https://www.redgifs.com/explore/niches"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.redgifs.com/",
}


def slugify_tag(name: str) -> str:
    """
    Convert a display tag/niche name into a RedGIFs-style slug.

    Example:
        "Pegging POV" -> "pegging-pov"
        "r/CaughtPublic" -> "caughtpublic"
    """
    name = name.strip()
    name = re.sub(r"^r/", "", name, flags=re.IGNORECASE)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def clean_text(value: Any) -> str:
    value = str(value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_items_from_payload(payload: Any) -> list[dict[str, Any]]:
    """
    RedGIFs endpoints can return slightly different shapes.

    Common shapes:
        {"tags": [...]}
        {"niches": [...]}
        {"items": [...]}
        {"results": [...]}
        [...]
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("niches", "tags", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def normalize_item(item: dict[str, Any], rank: int) -> dict[str, Any] | None:
    """
    Normalize a RedGIFs tag/niche object into one consistent JSON shape.
    """
    name = (
        item.get("name")
        or item.get("tag")
        or item.get("text")
        or item.get("title")
        or item.get("id")
        or item.get("slug")
    )

    name = clean_text(name)

    slug = (
        item.get("slug")
        or item.get("id")
        or item.get("name")
        or item.get("tag")
        or item.get("text")
    )

    slug = slugify_tag(clean_text(slug))

    if not slug:
        return None

    if not name:
        name = slug.replace("-", " ").title()

    count = (
        item.get("count")
        or item.get("total")
        or item.get("gifs")
        or item.get("gifCount")
        or item.get("items")
    )

    image = (
        item.get("image")
        or item.get("poster")
        or item.get("thumbnail")
        or item.get("preview")
    )

    return {
        "rank": rank,
        "name": name,
        "slug": slug,
        "url": f"https://www.redgifs.com/niches/{slug}",
        "count": count,
        "image": image,
        "raw": item,
    }


class RedGifsClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.token: str | None = None

    async def get_token(self) -> str:
        async with self.session.get(TOKEN_URL, headers=HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()

        token = data.get("token")
        if not token:
            raise RuntimeError("Temporary token response did not contain a token")

        self.token = token
        return token

    async def api_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        if not self.token:
            await self.get_token()

        url = f"{BASE_API}{path}"
        headers = {**HEADERS, "Authorization": f"Bearer {self.token}"}

        for attempt in range(4):
            try:
                async with self.session.get(
                    url, headers=headers, params=params
                ) as resp:
                    if resp.status == 401:
                        await self.get_token()
                        headers["Authorization"] = f"Bearer {self.token}"
                        continue

                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_for = (
                            float(retry_after)
                            if retry_after and retry_after.isdigit()
                            else 2.0 + attempt
                        )
                        print(f"Rate limited. Sleeping {wait_for:.1f}s...")
                        await asyncio.sleep(wait_for)
                        continue

                    resp.raise_for_status()
                    return await resp.json()

            except ClientResponseError as exc:
                if exc.status in {500, 502, 503, 504} and attempt < 3:
                    wait_for = 1.0 + attempt
                    print(
                        f"Temporary API error HTTP {exc.status}. "
                        f"Retrying in {wait_for:.1f}s..."
                    )
                    await asyncio.sleep(wait_for)
                    continue

                raise

        raise RuntimeError(f"Failed API request after retries: {url}")


async def get_trending_tags(client: RedGifsClient) -> list[dict[str, Any]]:
    """
    Try common RedGIFs trending/tag endpoints.

    RedGIFs changes endpoint names sometimes, so this tries several likely
    endpoints and uses the first one that returns a usable list.
    """
    attempts = [
        ("/tags/trending", None),
        ("/tags", None),
        ("/tags/search", {"search_text": ""}),
        ("/search/tags", {"search_text": ""}),
    ]

    errors: list[str] = []

    for endpoint, params in attempts:
        try:
            payload = await client.api_get(endpoint, params=params)
            items = extract_items_from_payload(payload)

            if items:
                print(f"Using API endpoint: {endpoint}")
                return items

        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")

    print("Tag API attempts failed or returned no items:")
    for error in errors:
        print(f"  - {error}")

    return []


async def search_niches_from_tags(
    client: RedGifsClient,
    tags: list[dict[str, Any]],
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    """
    Optional enrichment pass.

    Starting from trending tags, try RedGIFs niche search endpoints to find
    actual niche objects. If those endpoints fail, the caller can still use
    the normalized tag objects as niche-style entries.
    """
    niche_results: list[dict[str, Any]] = []
    seen: set[str] = set()

    niche_search_endpoints = [
        "/niches/search",
        "/search/niches",
    ]

    for tag in tags:
        name = clean_text(tag.get("name") or tag.get("tag") or tag.get("text"))
        if not name:
            continue

        for endpoint in niche_search_endpoints:
            try:
                payload = await client.api_get(
                    endpoint,
                    params={
                        "search_text": name,
                        "query": name,
                        "count": 10,
                        "page": 1,
                    },
                )

                items = extract_items_from_payload(payload)

                for item in items:
                    normalized = normalize_item(item, len(niche_results) + 1)

                    if not normalized:
                        continue

                    slug = normalized["slug"]

                    if slug in seen:
                        continue

                    seen.add(slug)
                    niche_results.append(normalized)

                    if limit is not None and len(niche_results) >= limit:
                        return niche_results

                if items:
                    break

            except Exception:
                continue

    return niche_results


async def scan_explore_page_for_niche_links(
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """
    Fallback: fetch the normal explore page HTML and extract /niches/<slug> links.

    This will only work if the server-rendered HTML contains niche links.
    If the page is fully client-rendered, this may return zero items.
    """
    try:
        async with session.get(
            EXPLORE_NICHES_URL,
            headers={**HEADERS, "Accept": "text/html,*/*"},
        ) as resp:
            resp.raise_for_status()
            html = await resp.text(errors="ignore")
    except Exception as exc:
        print(f"Could not fetch explore page fallback: {exc}")
        return []

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    for href in re.findall(r"""href=["']([^"']*/niches/[^"']+)["']""", html):
        absolute_url = urljoin(EXPLORE_NICHES_URL, href)
        parsed = urlparse(absolute_url)
        parts = [part for part in parsed.path.split("/") if part]

        if len(parts) < 2 or parts[0] != "niches":
            continue

        slug = parts[1].strip()

        if not slug or slug.lower() in seen:
            continue

        seen.add(slug.lower())

        found.append(
            {
                "name": slug.replace("-", " ").title(),
                "slug": slug,
                "url": f"https://www.redgifs.com/niches/{slug}",
            }
        )

    return found


async def scrape_redgifs_niches(
    *,
    output: Path,
    limit: int | None,
    enrich: bool,
) -> None:
    timeout = ClientTimeout(total=None, sock_connect=30, sock_read=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        client = RedGifsClient(session)

        raw_tags = await get_trending_tags(client)

        niches: list[dict[str, Any]] = []

        if enrich and raw_tags:
            print("Trying to enrich trending tags with niche search...")
            niches = await search_niches_from_tags(client, raw_tags, limit=limit)

        if not niches:
            print("Using trending tags as niche-style entries...")
            seen: set[str] = set()

            for item in raw_tags:
                normalized = normalize_item(item, len(niches) + 1)

                if not normalized:
                    continue

                slug = normalized["slug"]

                if slug in seen:
                    continue

                seen.add(slug)
                niches.append(normalized)

                if limit is not None and len(niches) >= limit:
                    break

        if not niches:
            print("Trying HTML fallback from /explore/niches...")
            fallback_items = await scan_explore_page_for_niche_links(session)

            for item in fallback_items:
                normalized = normalize_item(item, len(niches) + 1)

                if normalized:
                    niches.append(normalized)

                if limit is not None and len(niches) >= limit:
                    break

    data = {
        "source_url": EXPLORE_NICHES_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "count": len(niches),
        "niches": niches,
    }

    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    print(f"Saved {len(niches)} items to {output}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape RedGIFs top/trending tags or niches into JSON.",
    )

    parser.add_argument(
        "-o",
        "--output",
        default="redgifs_niches.json",
        help="Output JSON file path",
    )

    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Maximum number of items to save",
    )

    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Do not try niche-search enrichment; save trending tags directly",
    )

    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    asyncio.run(
        scrape_redgifs_niches(
            output=Path(args.output),
            limit=args.limit,
            enrich=not args.no_enrich,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

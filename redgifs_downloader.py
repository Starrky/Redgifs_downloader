#!/usr/bin/env python3
"""
RedGIFs bulk downloader with automatic source folders.

Examples:
    python redgifs_downloader.py "https://www.redgifs.com/watch/SomeSlug"

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --limit 50
    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --limit 50 --order top

    python redgifs_downloader.py "https://www.redgifs.com/niches/someniche" --limit 25 --order hot
    python redgifs_downloader.py "https://www.redgifs.com/niches/femboy" --limit 100 --blacklist furry

    python redgifs_downloader.py "https://www.redgifs.com/search?query=example" --limit 20
    python redgifs_downloader.py "https://www.redgifs.com/search?query=example&order=latest" --limit 20

Default folders:
    - User URL:   downloads/<username>/
    - Niche URL:  downloads/<niche>/
    - Search/tag: downloads/<search_term>/
    - Single URL: downloads/

Notes:
    - Use this only for content you are allowed to download.
    - RedGIFs may change or restrict its API.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout


BASE_API = "https://api.redgifs.com/v2"
TOKEN_URL = f"{BASE_API}/auth/temporary"

VALID_ORDERS = ("top", "hot", "latest")

# Add blacklist terms here if you always want them applied.
# Command-line --blacklist and --blacklist-file values are added on top.
DEFAULT_BLACKLIST = [
    "Furry",
    "Furries",
    "Elf",
    "MILF",
    "Pawg",
    "Ahegao",


]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.redgifs.com/",
}

WATCH_RE = re.compile(
    r"(?:https?://(?:www\.)?redgifs\.com)?/(?:watch|ifr)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
MULTI_SOURCE_RE = re.compile(r"\s*[,;|]\s*")
REDGIFS_HOST_RE = re.compile(r"^(?:www\.)?redgifs\.com/", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    slug: str
    filename: str | None = None
    info: dict[str, Any] | None = None


def safe_filename(name: str) -> str:
    """Return a filesystem-safe filename stem."""
    name = unquote(name).strip()
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)
    return name.strip("._") or "redgifs_video"


def parse_watch_slug(url_or_slug: str) -> str | None:
    """Extract a RedGIFs watch/iframe slug, or accept a bare slug."""
    value = url_or_slug.strip()
    parsed = urlparse(value)

    if not parsed.scheme and "/" not in value and value:
        return value.split("?")[0].split("#")[0].split(";")[0]

    match = WATCH_RE.search(value)
    if match:
        return match.group(1).split("?")[0].split("#")[0].split(";")[0]

    return None


def split_input_sources(input_value: str) -> list[str]:
    """Split one CLI value into one or more sources."""
    parts = [part.strip() for part in MULTI_SOURCE_RE.split(input_value) if part.strip()]
    return parts or [input_value.strip()]


def normalize_input_source(source: str, *, bare_is_niche: bool) -> str:
    """
    Normalize one input source.

    When multiple sources are supplied, bare values like "femboy" are treated
    as niche slugs so comma-separated niche lists are ergonomic.
    """
    source = source.strip()

    if REDGIFS_HOST_RE.match(source):
        return f"https://{source}"

    parsed = urlparse(source)
    if parsed.scheme or not bare_is_niche or "/" in source:
        return source

    return f"https://www.redgifs.com/niches/{quote(source)}"


def expand_input_sources(input_value: str) -> list[str]:
    """Return normalized download sources from the positional CLI input."""
    parts = split_input_sources(input_value)
    bare_is_niche = len(parts) > 1
    return [
        normalize_input_source(part, bare_is_niche=bare_is_niche)
        for part in parts
    ]


def get_order_from_url(input_url: str, fallback: str) -> str:
    """
    Read ?order=top, ?order=hot, or ?order=latest from the URL.

    If the URL has no valid order value, use the CLI/default fallback.
    """
    parsed = urlparse(input_url)
    query_values = parse_qs(parsed.query)
    order = query_values.get("order", [None])[0]

    if order in VALID_ORDERS:
        return order

    return fallback


def infer_collection_folder(input_url: str) -> str | None:
    """
    Return the default subfolder name for collection-style URLs.

    Examples:
        https://www.redgifs.com/users/alice -> alice
        https://www.redgifs.com/niches/example -> example
        https://www.redgifs.com/search?query=cat -> cat
        https://www.redgifs.com/tags/cat -> cat

    Single watch/iframe URLs intentionally return None so they save directly
    into the selected output directory unless --folder is used.
    """
    if parse_watch_slug(input_url):
        return None

    parsed = urlparse(input_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    lower_parts = [part.lower() for part in path_parts]

    for collection_name in ("users", "niches", "tags", "tag"):
        if collection_name in lower_parts:
            idx = lower_parts.index(collection_name)
            if idx + 1 < len(path_parts):
                return safe_filename(path_parts[idx + 1])

    query_values = parse_qs(parsed.query)
    search_text = (
        query_values.get("query", [None])[0]
        or query_values.get("q", [None])[0]
        or query_values.get("search_text", [None])[0]
        or query_values.get("tag", [None])[0]
    )

    if search_text:
        return safe_filename(search_text)

    if path_parts:
        return safe_filename(path_parts[-1])

    return None


def extract_gifs(payload: Any) -> list[dict[str, Any]]:
    """Handle the common shapes returned by RedGIFs list endpoints."""
    if not isinstance(payload, dict):
        return []

    for key in ("gifs", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    gif = payload.get("gif")
    if isinstance(gif, dict):
        return [gif]

    return []


def iter_tag_texts(value: Any) -> list[str]:
    """Extract readable tag names from RedGIFs tag/list shapes."""
    tags: list[str] = []

    if isinstance(value, str):
        clean = value.strip()
        if clean:
            tags.append(clean)
        return tags

    if isinstance(value, dict):
        for key in ("name", "tag", "text", "title", "slug", "id"):
            tag = value.get(key)
            if isinstance(tag, str) and tag.strip():
                tags.append(tag.strip())
        return tags

    if isinstance(value, list):
        for item in value:
            tags.extend(iter_tag_texts(item))

    return tags


def extract_tag_values(gif: dict[str, Any]) -> list[str]:
    """Return all tag-like values from a RedGIFs GIF metadata object."""
    tags: list[str] = []

    for key in ("tags", "niches", "categories"):
        if key in gif:
            tags.extend(iter_tag_texts(gif[key]))

    return tags


def first_blacklist_match(
    tags: list[str],
    blacklist_terms: list[str],
) -> tuple[str, str] | None:
    """Return the first (blacklist term, video tag) match, case-insensitively."""
    for tag in tags:
        tag_lower = tag.casefold()
        for term in blacklist_terms:
            if term in tag_lower:
                return term, tag

    return None


def parse_blacklist_terms(values: list[str]) -> list[str]:
    """Parse repeated and comma-separated blacklist CLI values."""
    terms: list[str] = []
    seen: set[str] = set()

    for value in values:
        for part in re.split(r"[,\n]", value):
            term = part.strip().casefold()

            if term and term not in seen:
                seen.add(term)
                terms.append(term)

    return terms


def load_blacklist_terms(
    values: list[str],
    blacklist_file: str | None,
) -> list[str]:
    """Load blacklist terms from CLI values and an optional text file."""
    raw_values = [*DEFAULT_BLACKLIST, *values]

    if blacklist_file:
        file_path = Path(blacklist_file)
        file_values = []

        for line in file_path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip()
            if value:
                file_values.append(value)

        raw_values.extend(file_values)

    return parse_blacklist_terms(raw_values)


def parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After seconds or HTTP date values."""
    if not value:
        return None

    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


async def wait_for_rate_limit(
    headers: aiohttp.typedefs.LooseHeaders,
    *,
    fallback: float,
    context: str,
) -> None:
    """Sleep for RedGIFs rate-limit recovery, honoring Retry-After when present."""
    retry_after = None

    if hasattr(headers, "get"):
        retry_after = headers.get("Retry-After")  # type: ignore[union-attr]

    parsed_retry_after = parse_retry_after(retry_after)
    wait_for = parsed_retry_after if parsed_retry_after is not None else fallback
    print(f"{context} rate limited. Sleeping {wait_for:.1f}s...")
    await asyncio.sleep(wait_for)


def get_next_page(payload: dict[str, Any], current_page: int) -> int | None:
    """Infer the next page number from a response, if pagination should continue."""
    pages = payload.get("pages")
    if isinstance(pages, int) and current_page < pages:
        return current_page + 1

    page_info = payload.get("page")
    if isinstance(page_info, dict):
        total_pages = page_info.get("totalPages") or page_info.get("pages")
        if isinstance(total_pages, int) and current_page < total_pages:
            return current_page + 1

    if extract_gifs(payload):
        return current_page + 1

    return None


class RedGifsClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        page_size: int = 80,
        delay: float = 0.15,
    ) -> None:
        self.session = session
        self.page_size = page_size
        self.delay = delay
        self.token: str | None = None

    async def get_token(self) -> str:
        attempt = 0

        while True:
            async with self.session.get(TOKEN_URL, headers=HEADERS) as resp:
                if resp.status == 429:
                    await wait_for_rate_limit(
                        resp.headers,
                        fallback=min(60.0, 2.0 + attempt),
                        context="Token request",
                    )
                    attempt += 1
                    continue

                resp.raise_for_status()
                data = await resp.json()
                break

        token = data.get("token")
        if not token:
            raise RuntimeError("Temporary token response did not contain a token")

        self.token = token
        print(f"Got token: {token[:8]}...")
        return token

    async def api_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            await self.get_token()

        url = f"{BASE_API}{path}"
        headers = {**HEADERS, "Authorization": f"Bearer {self.token}"}

        attempt = 0

        while True:
            try:
                async with self.session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 401:
                        await self.get_token()
                        headers["Authorization"] = f"Bearer {self.token}"
                        continue

                    if resp.status == 429:
                        await wait_for_rate_limit(
                            resp.headers,
                            fallback=min(60.0, 2.0 + attempt),
                            context="API request",
                        )
                        attempt += 1
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
                    attempt += 1
                    continue

                raise

            if self.delay:
                await asyncio.sleep(self.delay)

    async def get_video_info(self, slug: str) -> dict[str, Any] | None:
        try:
            payload = await self.api_get(f"/gifs/{quote(slug)}")
            gif = payload.get("gif")
            return gif if isinstance(gif, dict) else None
        except Exception as exc:
            print(f"Failed to get metadata for {slug}: {exc}")
            return None

    async def get_blacklist_checked_info(
        self,
        slug: str,
        gif: dict[str, Any],
        blacklist_terms: list[str],
    ) -> tuple[bool, dict[str, Any] | None]:
        """
        Return whether a video is allowed by the blacklist.

        When a blacklist is active, full metadata is fetched before accepting
        the target so rejected videos do not count toward --limit.
        """
        if not blacklist_terms:
            return True, None

        match = first_blacklist_match(extract_tag_values(gif), blacklist_terms)
        if match:
            term, tag = match
            print(f"Skipping {slug}, blacklisted tag '{tag}' matched '{term}'")
            return False, None

        info = await self.get_video_info(slug)
        if not info:
            print(f"Skipping {slug}, could not verify tags against blacklist")
            return False, None

        match = first_blacklist_match(extract_tag_values(info), blacklist_terms)
        if match:
            term, tag = match
            print(f"Skipping {slug}, blacklisted tag '{tag}' matched '{term}'")
            return False, None

        return True, info

    async def iter_endpoint(
        self,
        endpoint: str,
        *,
        limit: int | None,
        extra_params: dict[str, Any] | None = None,
        blacklist_terms: list[str] | None = None,
    ) -> list[Target]:
        blacklist_terms = blacklist_terms or []
        found: list[Target] = []
        seen: set[str] = set()
        page = 1
        params = {"count": self.page_size, **(extra_params or {})}

        while True:
            params["page"] = page

            try:
                payload = await self.api_get(endpoint, params=params)
            except Exception as exc:
                print(f"Could not fetch page {page} from {endpoint}: {exc}")
                break

            page_gifs = extract_gifs(payload)
            if not page_gifs:
                break

            for gif in page_gifs:
                slug = (
                    gif.get("id")
                    or gif.get("slug")
                    or gif.get("name")
                    or gif.get("gifId")
                )

                if not slug:
                    urls = gif.get("urls") if isinstance(gif.get("urls"), dict) else {}
                    sd_or_hd = urls.get("hd") or urls.get("sd")
                    if sd_or_hd:
                        slug = Path(urlparse(sd_or_hd).path).stem

                if not slug:
                    continue

                slug = str(slug)

                if slug.lower() in seen:
                    continue

                seen.add(slug.lower())
                allowed, info = await self.get_blacklist_checked_info(
                    slug,
                    gif,
                    blacklist_terms,
                )
                if not allowed:
                    continue

                found.append(Target(slug=slug, info=info))
                print(f"Found {len(found)}: {slug}")

                if limit is not None and len(found) >= limit:
                    return found

            next_page = get_next_page(payload, page)
            if not next_page or next_page <= page:
                break

            page = next_page

        return found


async def scan_page_for_watch_links(
    client: RedGifsClient,
    session: aiohttp.ClientSession,
    start_url: str,
    *,
    limit: int | None,
    max_pages: int,
    blacklist_terms: list[str] | None = None,
) -> list[Target]:
    """
    Fallback crawler: load pages from the supplied RedGIFs URL and extract
    /watch/ or /ifr/ links.

    It stays on redgifs.com.
    """
    queue = [start_url]
    visited: set[str] = set()
    seen_slugs: set[str] = set()
    targets: list[Target] = []
    blacklist_terms = blacklist_terms or []

    while queue and len(visited) < max_pages:
        url = queue.pop(0)

        if url in visited:
            continue

        visited.add(url)

        try:
            async with session.get(
                url,
                headers={**HEADERS, "Accept": "text/html,*/*"},
            ) as resp:
                if resp.status == 429:
                    await wait_for_rate_limit(
                        resp.headers,
                        fallback=30.0,
                        context="Page scan",
                    )
                    visited.remove(url)
                    queue.insert(0, url)
                    continue

                if resp.status >= 400:
                    print(f"Skipping page {url}: HTTP {resp.status}")
                    continue

                html = await resp.text(errors="ignore")

        except Exception as exc:
            print(f"Could not scan {url}: {exc}")
            continue

        for match in WATCH_RE.finditer(html):
            slug = match.group(1)
            key = slug.lower()

            if key not in seen_slugs:
                seen_slugs.add(key)

                allowed, info = await client.get_blacklist_checked_info(
                    slug,
                    {},
                    blacklist_terms,
                )
                if not allowed:
                    continue

                targets.append(Target(slug=slug, info=info))
                print(f"Found {len(targets)} by scanning links: {slug}")

                if limit is not None and len(targets) >= limit:
                    return targets

        for href in re.findall(r"""href=["']([^"']+)["']""", html, flags=re.IGNORECASE):
            next_url = urljoin(url, href)
            parsed = urlparse(next_url)

            if parsed.netloc.lower() not in {"redgifs.com", "www.redgifs.com"}:
                continue

            if "/watch/" in parsed.path or "/ifr/" in parsed.path:
                continue

            if any(part in parsed.path for part in ("/users/", "/niches/", "/search")):
                normalized = parsed._replace(fragment="").geturl()

                if normalized not in visited and normalized not in queue:
                    queue.append(normalized)

    return targets


async def targets_from_url(
    client: RedGifsClient,
    session: aiohttp.ClientSession,
    input_url: str,
    *,
    limit: int | None,
    max_scan_pages: int,
    order: str,
    blacklist_terms: list[str] | None = None,
) -> list[Target]:
    blacklist_terms = blacklist_terms or []
    single_slug = parse_watch_slug(input_url)
    if single_slug:
        allowed, info = await client.get_blacklist_checked_info(
            single_slug,
            {},
            blacklist_terms,
        )
        return [Target(slug=single_slug, info=info)] if allowed else []

    order = get_order_from_url(input_url, order)

    parsed = urlparse(input_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    lower_parts = [part.lower() for part in path_parts]

    if "users" in lower_parts:
        idx = lower_parts.index("users")
        if idx + 1 < len(path_parts):
            username = path_parts[idx + 1].lower()
            print(f"Detected user page: {username}")
            print(f"Using order: {order}")

            targets = await client.iter_endpoint(
                f"/users/{quote(username)}/search",
                limit=limit,
                extra_params={"order": order},
                blacklist_terms=blacklist_terms,
            )

            if targets:
                return targets

    if "niches" in lower_parts:
        idx = lower_parts.index("niches")
        if idx + 1 < len(path_parts):
            niche = path_parts[idx + 1]
            print(f"Detected niche page: {niche}")
            print(f"Using order: {order}")

            targets = await client.iter_endpoint(
                f"/niches/{quote(niche)}/gifs",
                limit=limit,
                extra_params={"order": order},
                blacklist_terms=blacklist_terms,
            )

            if targets:
                return targets

    query_values = parse_qs(parsed.query)
    search_text = (
        query_values.get("query", [None])[0]
        or query_values.get("q", [None])[0]
        or query_values.get("search_text", [None])[0]
    )

    if "search" in lower_parts and search_text:
        print(f"Detected search page: {search_text}")
        print(f"Using order: {order}")

        targets = await client.iter_endpoint(
            "/gifs/search",
            limit=limit,
            extra_params={
                "search_text": search_text,
                "order": order,
            },
            blacklist_terms=blacklist_terms,
        )

        if targets:
            return targets

    print("API collection detection did not match or returned nothing; scanning page links...")

    return await scan_page_for_watch_links(
        client,
        session,
        input_url,
        limit=limit,
        max_pages=max_scan_pages,
        blacklist_terms=blacklist_terms,
    )


async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    output_path: Path,
    *,
    chunk_size: int = 1024 * 256,
) -> None:
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    attempt = 0

    while True:
        async with session.get(url, headers=HEADERS) as resp:
            if resp.status == 429:
                await wait_for_rate_limit(
                    resp.headers,
                    fallback=min(120.0, 5.0 + attempt * 5.0),
                    context="Download",
                )
                attempt += 1
                continue

            resp.raise_for_status()

            with tmp_path.open("wb") as file:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    if chunk:
                        file.write(chunk)

            break

    tmp_path.replace(output_path)


async def download_targets(
    input_url: str,
    *,
    output_dir: Path,
    limit: int | None,
    concurrency: int,
    page_size: int,
    max_scan_pages: int,
    overwrite: bool,
    prefer: str,
    make_subfolder: bool,
    folder_override: str | None,
    order: str,
    blacklist_terms: list[str],
) -> None:
    folder_name = (
        safe_filename(folder_override)
        if folder_override
        else infer_collection_folder(input_url)
        if make_subfolder
        else None
    )

    save_dir = output_dir / folder_name if folder_name else output_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    timeout = ClientTimeout(total=None, sock_connect=30, sock_read=120)
    connector = aiohttp.TCPConnector(limit_per_host=max(concurrency, 1))

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = RedGifsClient(session, page_size=page_size)

        targets = await targets_from_url(
            client,
            session,
            input_url,
            limit=limit,
            max_scan_pages=max_scan_pages,
            order=order,
            blacklist_terms=blacklist_terms,
        )

        if limit is not None:
            targets = targets[:limit]

        if not targets:
            print("No RedGIFs videos found.")
            return

        print(f"Preparing to download {len(targets)} video(s) into: {save_dir}")

        semaphore = asyncio.Semaphore(concurrency)

        async def worker(target: Target) -> None:
            async with semaphore:
                slug = target.slug
                filename = target.filename or f"{safe_filename(slug)}.mp4"
                output_path = save_dir / filename

                if output_path.exists() and not overwrite:
                    print(f"Skipping {slug}, already exists: {output_path}")
                    return

                info = target.info or await client.get_video_info(slug)

                if not info:
                    print(f"Could not get video info for {slug}")
                    return

                if blacklist_terms:
                    match = first_blacklist_match(
                        extract_tag_values(info),
                        blacklist_terms,
                    )
                    if match:
                        term, tag = match
                        print(
                            f"Skipping {slug}, blacklisted tag '{tag}' "
                            f"matched '{term}'"
                        )
                        return

                urls = info.get("urls") if isinstance(info.get("urls"), dict) else {}

                if prefer == "hd":
                    video_url = urls.get("hd") or urls.get("sd")
                else:
                    video_url = urls.get("sd") or urls.get("hd")

                if not video_url:
                    print(f"No downloadable HD/SD URL found for {slug}")
                    return

                try:
                    await download_file(session, video_url, output_path)
                    print(f"Downloaded {slug} -> {output_path}")
                except Exception as exc:
                    print(f"Error downloading {slug}: {exc}")

        await asyncio.gather(*(worker(target) for target in targets))


async def download_input_sources(
    input_sources: list[str],
    *,
    output_dir: Path,
    limit: int | None,
    concurrency: int,
    page_size: int,
    max_scan_pages: int,
    overwrite: bool,
    prefer: str,
    make_subfolder: bool,
    folder_override: str | None,
    order: str,
    blacklist_terms: list[str],
) -> None:
    if len(input_sources) > 1:
        print(f"Downloading from {len(input_sources)} source(s).")

        if folder_override:
            print("Ignoring --folder for multiple sources so each source keeps its own folder.")
            folder_override = None

    for index, input_source in enumerate(input_sources, start=1):
        if len(input_sources) > 1:
            print(f"\n[{index}/{len(input_sources)}] Source: {input_source}")

        await download_targets(
            input_source,
            output_dir=output_dir,
            limit=limit,
            concurrency=concurrency,
            page_size=page_size,
            max_scan_pages=max_scan_pages,
            overwrite=overwrite,
            prefer=prefer,
            make_subfolder=make_subfolder,
            folder_override=folder_override,
            order=order,
            blacklist_terms=blacklist_terms,
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download one or more RedGIFs videos from a watch/user/niche/search URL.",
    )

    parser.add_argument(
        "url",
        help=(
            "RedGIFs watch, user, niche, or search URL. "
            "Use comma, semicolon, or | to download multiple niche slugs/URLs."
        ),
    )

    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Maximum number of videos to download",
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        default="downloads",
        help="Directory to save videos into",
    )

    parser.add_argument(
        "--folder",
        default=None,
        help=(
            "Override the automatic subfolder name, e.g. --folder my_tag. "
            "Videos will be saved into <output-dir>/<folder>."
        ),
    )

    parser.add_argument(
        "--flat",
        action="store_true",
        help="Disable automatic username/tag/niche subfolders and save directly into --output-dir",
    )

    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent downloads",
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=80,
        help="API page size for collection fetching",
    )

    parser.add_argument(
        "--max-scan-pages",
        type=int,
        default=20,
        help="Maximum same-site HTML pages to scan when API detection fails",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download files even if they already exist",
    )

    parser.add_argument(
        "--prefer",
        choices=("hd", "sd"),
        default="hd",
        help="Prefer HD or SD video URL when both are available",
    )

    parser.add_argument(
        "--order",
        choices=VALID_ORDERS,
        default="latest",
        help="Sort order for user, niche, and search URLs",
    )

    parser.add_argument(
        "--blacklist",
        action="append",
        default=[],
        metavar="TAG[,TAG...]",
        help=(
            "Skip videos whose tags contain any listed term. "
            "Can be repeated or comma-separated."
        ),
    )

    parser.add_argument(
        "--blacklist-file",
        default=None,
        help="Text file of blacklist terms, one per line. Lines may use # comments.",
    )

    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")

    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    if args.page_size < 1:
        parser.error("--page-size must be at least 1")

    if args.max_scan_pages < 1:
        parser.error("--max-scan-pages must be at least 1")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        blacklist_terms = load_blacklist_terms(args.blacklist, args.blacklist_file)
    except OSError as exc:
        print(f"Could not load blacklist file: {exc}", file=sys.stderr)
        return 2

    if blacklist_terms:
        print(f"Using blacklist: {', '.join(blacklist_terms)}")

    input_sources = expand_input_sources(args.url)

    asyncio.run(
        download_input_sources(
            input_sources,
            output_dir=Path(args.output_dir),
            limit=args.limit,
            concurrency=args.concurrency,
            page_size=args.page_size,
            max_scan_pages=args.max_scan_pages,
            overwrite=args.overwrite,
            prefer=args.prefer,
            make_subfolder=not args.flat,
            folder_override=args.folder,
            order=args.order,
            blacklist_terms=blacklist_terms,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

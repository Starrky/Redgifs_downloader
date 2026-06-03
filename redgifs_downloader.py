#!/usr/bin/env python3
"""
RedGIFs bulk downloader with automatic source folders.

Examples:
    python redgifs_downloader_improved.py "https://www.redgifs.com/watch/SomeSlug"
    python redgifs_downloader_improved.py "https://www.redgifs.com/users/someuser" --limit 50
    python redgifs_downloader_improved.py "https://www.redgifs.com/niches/someniche" --limit 25
    python redgifs_downloader_with_folders.py "https://www.redgifs.com/search?query=example" --limit 20

Default folders:
    - User URL:   downloads/<username>/
    - Niche URL:  downloads/<niche>/
    - Search/tag: downloads/<tag-or-query>/
    - Single URL: downloads/

Notes:
    - Use this only for content you are allowed to download.
    - RedGIFs may change or restrict its API. If endpoints stop working, the
      script falls back to scanning links from the supplied page where possible.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout


BASE_API = "https://api.redgifs.com/v2"
TOKEN_URL = f"{BASE_API}/auth/temporary"

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


@dataclass(frozen=True)
class Target:
    slug: str
    filename: str | None = None


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


def infer_collection_folder(input_url: str) -> str | None:
    """
    Return the default subfolder name for collection-style URLs.

    Examples:
        https://www.redgifs.com/users/alice       -> alice
        https://www.redgifs.com/niches/example    -> example
        https://www.redgifs.com/search?query=cat  -> cat
        https://www.redgifs.com/tags/cat          -> cat

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

    # Last-resort collection folder: use the last path segment for non-watch URLs.
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

    # Fallback: if a page returned results, optimistically try the next page.
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
        async with self.session.get(TOKEN_URL, headers=HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
            token = data.get("token")
            if not token:
                raise RuntimeError("Temporary token response did not contain a token")
            self.token = token
            print(f"Got token: {token[:8]}...")
            return token

    async def api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            await self.get_token()

        url = f"{BASE_API}{path}"
        headers = {**HEADERS, "Authorization": f"Bearer {self.token}"}

        for attempt in range(4):
            try:
                async with self.session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 401:
                        await self.get_token()
                        headers["Authorization"] = f"Bearer {self.token}"
                        continue

                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_for = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 + attempt
                        print(f"Rate limited. Sleeping {wait_for:.1f}s...")
                        await asyncio.sleep(wait_for)
                        continue

                    resp.raise_for_status()
                    return await resp.json()

            except ClientResponseError:
                raise

            if self.delay:
                await asyncio.sleep(self.delay)

        raise RuntimeError(f"Failed API request after retries: {url}")

    async def get_video_info(self, slug: str) -> dict[str, Any] | None:
        try:
            payload = await self.api_get(f"/gifs/{quote(slug)}")
            gif = payload.get("gif")
            return gif if isinstance(gif, dict) else None
        except Exception as exc:
            print(f"Failed to get metadata for {slug}: {exc}")
            return None

    async def iter_endpoint(
        self,
        endpoint: str,
        *,
        limit: int | None,
        extra_params: dict[str, Any] | None = None,
    ) -> list[Target]:
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
                found.append(Target(slug=slug))
                print(f"Found {len(found)}: {slug}")

                if limit and len(found) >= limit:
                    return found

            next_page = get_next_page(payload, page)
            if not next_page or next_page <= page:
                break

            page = next_page

        return found


async def scan_page_for_watch_links(
    session: aiohttp.ClientSession,
    start_url: str,
    *,
    limit: int | None,
    max_pages: int,
) -> list[Target]:
    """
    Fallback crawler: load pages from the supplied RedGIFs URL and extract
    /watch/<slug> or /ifr/<slug> links. It stays on redgifs.com.
    """
    queue = [start_url]
    visited: set[str] = set()
    seen_slugs: set[str] = set()
    targets: list[Target] = []

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            async with session.get(url, headers={**HEADERS, "Accept": "text/html,*/*"}) as resp:
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
                targets.append(Target(slug=slug))
                print(f"Found {len(targets)} by scanning links: {slug}")

                if limit and len(targets) >= limit:
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
) -> list[Target]:
    single_slug = parse_watch_slug(input_url)
    if single_slug:
        return [Target(slug=single_slug)]

    parsed = urlparse(input_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    lower_parts = [part.lower() for part in path_parts]

    if "users" in lower_parts:
        idx = lower_parts.index("users")
        if idx + 1 < len(path_parts):
            username = path_parts[idx + 1].lower()
            print(f"Detected user page: {username}")
            targets = await client.iter_endpoint(
                f"/users/{quote(username)}/search",
                limit=limit,
                extra_params={"order": "new"},
            )
            if targets:
                return targets

    if "niches" in lower_parts:
        idx = lower_parts.index("niches")
        if idx + 1 < len(path_parts):
            niche = path_parts[idx + 1]
            print(f"Detected niche page: {niche}")
            targets = await client.iter_endpoint(
                f"/niches/{quote(niche)}/gifs",
                limit=limit,
                extra_params={"order": "new"},
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
        targets = await client.iter_endpoint(
            "/gifs/search",
            limit=limit,
            extra_params={"search_text": search_text, "order": "new"},
        )
        if targets:
            return targets

    print("API collection detection did not match or returned nothing; scanning page links...")
    return await scan_page_for_watch_links(
        session,
        input_url,
        limit=limit,
        max_pages=max_scan_pages,
    )


async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    output_path: Path,
    *,
    chunk_size: int = 1024 * 256,
) -> None:
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")

    async with session.get(url, headers=HEADERS) as resp:
        resp.raise_for_status()
        with tmp_path.open("wb") as file:
            async for chunk in resp.content.iter_chunked(chunk_size):
                if chunk:
                    file.write(chunk)

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
) -> None:
    folder_name = safe_filename(folder_override) if folder_override else (
        infer_collection_folder(input_url) if make_subfolder else None
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
        )

        if limit:
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

                info = await client.get_video_info(slug)
                if not info:
                    print(f"Could not get video info for {slug}")
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download one or more RedGIFs videos from a watch/user/niche/search URL.",
    )
    parser.add_argument("url", help="RedGIFs watch, user, niche, or search URL")
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
    asyncio.run(
        download_targets(
            args.url,
            output_dir=Path(args.output_dir),
            limit=args.limit,
            concurrency=args.concurrency,
            page_size=args.page_size,
            max_scan_pages=args.max_scan_pages,
            overwrite=args.overwrite,
            prefer=args.prefer,
            make_subfolder=not args.flat,
            folder_override=args.folder,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

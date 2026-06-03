# RedGIFs Downloader

A simple Python script for downloading one RedGIFs video or multiple videos from a user, niche, tag/search-style page.

> Use this only for content you are allowed to download. RedGIFs may change or restrict its API, so collection downloads can stop working if the site changes.

## Requirements

- Python 3.10+
- `aiohttp`

Install the dependency:

```bash
pip install aiohttp
```

## Basic usage

Download a single video:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/watch/SomeSlug"
```

Download videos from a user page:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser"
```

Download videos from a niche page:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/niches/someniche"
```

Download videos from a search/tag page:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/search?query=example"
```

## Limit downloads

Use `--limit` to stop after a certain number of videos:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser" --limit 50
```

## Output folders

By default, videos are saved into `downloads/`.

For collection-style links, the script creates a subfolder automatically:

```text
downloads/<username>/
downloads/<niche>/
downloads/<tag-or-search-query>/
```

Examples:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser"
```

saves to:

```text
downloads/someuser/
```

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/search?query=example"
```

saves to:

```text
downloads/example/
```

## Custom output directory

Use `-o` or `--output-dir`:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser" -o my_videos
```

This saves to:

```text
my_videos/someuser/
```

## Custom folder name

Use `--folder` to choose the subfolder manually:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser" --folder favorites
```

This saves to:

```text
downloads/favorites/
```

## Disable automatic subfolders

Use `--flat` to save directly into the output directory:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser" --flat
```

This saves to:

```text
downloads/
```

## Useful options

| Option | Description |
|---|---|
| `--limit N` | Download at most `N` videos |
| `-o`, `--output-dir DIR` | Choose the main output directory |
| `--folder NAME` | Force a specific subfolder name |
| `--flat` | Disable automatic username/tag/niche subfolders |
| `-c`, `--concurrency N` | Number of concurrent downloads |
| `--prefer hd` | Prefer HD video URLs; default |
| `--prefer sd` | Prefer SD video URLs |
| `--overwrite` | Re-download files that already exist |
| `--page-size N` | API page size for collection fetching |
| `--max-scan-pages N` | Number of HTML pages to scan if API detection fails |

## Examples

Download 25 videos from a user:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/users/someuser" --limit 25
```

Download 100 niche videos into a custom base folder:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/niches/someniche" --limit 100 -o videos
```

Download search results in SD with lower concurrency:

```bash
python redgifs_downloader_with_folders.py "https://www.redgifs.com/search?query=example" --prefer sd -c 2
```

## Troubleshooting

If no videos are found, check that the link is public and valid.

If downloads fail with API or rate-limit errors, try lowering concurrency:

```bash
python redgifs_downloader_with_folders.py "URL_HERE" -c 2
```

If the site changes its API, the script may need to be updated.

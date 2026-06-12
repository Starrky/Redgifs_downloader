# RedGIFs Downloader

A Python command-line downloader for RedGIFs videos.

It supports downloading from single video URLs, user pages, niche pages, search pages, and tag-style URLs. It can automatically create folders based on the source, limit downloads, choose a sort order, prefer HD or SD, skip existing files, overwrite files, and download multiple videos concurrently.

## Features

- Download a single RedGIFs video
- Bulk download from user pages
- Bulk download from niche pages
- Bulk download from search URLs
- Automatic folder naming
- Optional flat output mode
- Concurrent downloads
- HD/SD preference
- Skips existing files by default
- Optional overwrite mode
- Sort order support:
  - `latest`
  - `hot`
  - `top`

## Requirements

- Python 3.10+
- `aiohttp`

Install dependencies:

    pip install aiohttp

## Installation

Clone the repository:

    git clone https://github.com/Starrky/Redgifs_downloader.git
    cd Redgifs_downloader

Run the script directly:

    python redgifs_downloader.py "https://www.redgifs.com/watch/example"

Optional: create a shell shortcut.

For example, add this to `~/.zshrc` or `~/.bashrc`:

    redgifs() {
        python /full/path/to/Redgifs_downloader/redgifs_downloader.py "$@"
    }

Reload your shell:

    source ~/.zshrc

Then run:

    redgifs "https://www.redgifs.com/watch/example"

## Usage

    python redgifs_downloader.py URL [options]

Basic example:

    python redgifs_downloader.py "https://www.redgifs.com/watch/example"

## Examples

### Download a single video

    python redgifs_downloader.py "https://www.redgifs.com/watch/example"

The video will be saved into:

    downloads/

### Download from a user page

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser"

Videos will be saved into:

    downloads/someuser/

### Download from a niche

    python redgifs_downloader.py "https://www.redgifs.com/niches/example-niche"

Videos will be saved into:

    downloads/example-niche/

### Download from search

    python redgifs_downloader.py "https://www.redgifs.com/search?query=example"

Videos will be saved into:

    downloads/example/

### Limit the number of downloads

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --limit 50

or:

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" -l 50

### Sort results

Supported order values:

    latest
    hot
    top

Example:

    python redgifs_downloader.py "https://www.redgifs.com/niches/example-niche" --limit 50 --order top

You can also pass the order in the URL:

    python redgifs_downloader.py "https://www.redgifs.com/niches/example-niche?order=top" --limit 50

If both are provided, the URL order takes priority.

### Prefer SD instead of HD

By default, the script prefers HD when available.

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --prefer sd

### Change output directory

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --output-dir my_videos

or:

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" -o my_videos

### Override folder name

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --folder custom_folder

Videos will be saved into:

    downloads/custom_folder/

### Disable automatic subfolders

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --flat

Videos will be saved directly into:

    downloads/

### Increase concurrent downloads

Default concurrency is `5`.

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --concurrency 10

or:

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" -c 10

### Overwrite existing files

By default, existing files are skipped.

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser" --overwrite

## Options

| Option | Description |
| --- | --- |
| `url` | RedGIFs watch, user, niche, search, or tag URL |
| `-l`, `--limit` | Maximum number of videos to download |
| `-o`, `--output-dir` | Directory to save videos into |
| `--folder` | Override the automatic subfolder name |
| `--flat` | Disable automatic subfolders |
| `-c`, `--concurrency` | Number of concurrent downloads |
| `--page-size` | API page size for collection fetching |
| `--max-scan-pages` | Maximum pages to scan if API detection fails |
| `--overwrite` | Re-download files even if they already exist |
| `--prefer {hd,sd}` | Prefer HD or SD video URLs |
| `--order {top,hot,latest}` | Sort order for user, niche, and search URLs |
| `-h`, `--help` | Show help message |

## Output Structure

By default, the script creates folders automatically.

### User URL

Command:

    python redgifs_downloader.py "https://www.redgifs.com/users/someuser"

Output:

    downloads/someuser/

### Niche URL

Command:

    python redgifs_downloader.py "https://www.redgifs.com/niches/example-niche"

Output:

    downloads/example-niche/

### Search URL

Command:

    python redgifs_downloader.py "https://www.redgifs.com/search?query=example"

Output:

    downloads/example/

### Single video URL

Command:

    python redgifs_downloader.py "https://www.redgifs.com/watch/example"

Output:

    downloads/

## Troubleshooting

### `argument --order: expected one argument`

Use:

    python redgifs_downloader.py "https://www.redgifs.com/niches/example" --order=top

instead of:

    python redgifs_downloader.py "https://www.redgifs.com/niches/example" --order top

Both forms usually work, but `--order=top` can avoid issues with shell aliases or wrapper functions.

If you are using a shell shortcut like `redgifs`, make sure it forwards all arguments:

    redgifs() {
        python /full/path/to/redgifs_downloader.py "$@"
    }

Do not use only `$1`, because that only forwards the first argument.

### No videos found

Possible reasons:

- The URL format changed
- RedGIFs changed or restricted its API
- The page requires login or has access restrictions
- The niche, user, or search has no public videos
- Rate limiting occurred

Try lowering concurrency:

    python redgifs_downloader.py "URL" --concurrency 2

### Existing files are skipped

This is intentional. Use `--overwrite` to download them again:

    python redgifs_downloader.py "URL" --overwrite

## Notes

This tool uses RedGIFs public API endpoints and may stop working if RedGIFs changes its API or rate limits requests.

Use responsibly and only download content you are allowed to access and store.

## License

Add your preferred license here.

Example:

    MIT License

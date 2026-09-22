# psx-market-summary

Downloads the Pakistan Stock Exchange (PSX) daily market summary archive for
the current trading day (Asia/Karachi timezone), extracts the included listing
file, and records a per-day `.done` marker so each trading day is processed
exactly once.

Data source: `https://dps.psx.com.pk/download/mkt_summary/<YYYY-MM-DD>.Z`

## Usage

Plain script (what the GitHub Actions workflow runs):

```bash
pip install -r requirements.txt
python downloader.py
```

Command line with options (also available as the `psx-summary` console script
after `pip install .`):

```bash
psx-summary [--date YYYY-MM-DD] [--force] [--dry-run]
            [--output-dir DIR] [--marker-dir DIR]
```

| Option             | Meaning                                                                        |
|--------------------|--------------------------------------------------------------------------------|
| `--date YYYY-MM-DD`| Fetch a specific trading date instead of today (backfill). Explicit dates bypass the weekend check. |
| `--force`          | Re-download even if the day was already processed.                             |
| `--dry-run`        | Print the date, URL and planned action without downloading or writing anything.|
| `--output-dir DIR` | Directory for downloaded `.Z` archives (default `data/`, relative to the working directory). |
| `--marker-dir DIR` | Directory for `.done` markers (default `processed/`, relative to the working directory). |

Examples:

```bash
# Automatic daily run (skips weekends, skips days already processed)
python downloader.py

# Backfill a specific trading day
psx-summary --date 2026-09-18

# Re-download today's summary, overwriting the previous archive
psx-summary --force
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | Success, weekend, already processed, or dry run |
| 1    | Failure (network error, invalid archive, unsafe archive, I/O) |
| 2    | PSX has not published the file for the requested date yet |

## Behavior notes

- The date used is the current date in `Asia/Karachi`, regardless of where the
  script runs.
- The weekend check only applies to the automatic daily run. An explicit
  `--date` is always attempted (a missing file then reports exit code 2).
- The downloaded file is validated as a ZIP archive before it is written to
  disk. A corrupt download is never saved.
- Transient network errors are retried up to 3 times with backoff.
- ZIP member names are validated before extraction: absolute paths, drive
  letters and `..` components are rejected (zip-slip protection), so an
  archive can never write outside the output directory.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
ruff check .
```
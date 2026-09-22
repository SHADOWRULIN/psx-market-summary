#!/usr/bin/env python3
"""PSX market summary downloader.

Downloads the Pakistan Stock Exchange (PSX) daily market summary archive
(https://dps.psx.com.pk/download/mkt_summary/<YYYY-MM-DD>.Z), extracts the
included listing file and records a per-day ``.done`` marker so each trading
day is processed exactly once.

Can be used as a plain script (``python downloader.py``), as a module
(``python -m downloader``) or as the installed ``psx-summary`` console
script. See README.md for the CLI reference and exit-code table.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import requests

from config import BASE_URL, DOWNLOAD_FOLDER, MARKER_FOLDER

KARACHI = ZoneInfo("Asia/Karachi")

DOWNLOAD_SUFFIX = ".Z"
MARKER_SUFFIX = ".done"

DEFAULT_TIMEOUT = 60  # seconds, per HTTP request
DEFAULT_RETRIES = 3  # attempts for transient network errors
RETRY_BACKOFF = 2.0  # seconds, scaled by attempt number

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Referer": "https://dps.psx.com.pk/downloads",
}

# Exit codes (documented in README):
EXIT_OK = 0  # success, weekend, already processed or dry run
EXIT_ERROR = 1  # real failure: network, invalid archive, I/O, ...
EXIT_NOT_UPLOADED = 2  # PSX has not published the file for that date yet


class ZipSlipError(ValueError):
    """Raised when a ZIP member name escapes the extraction directory."""


def market_date(now: datetime | None = None) -> date:
    """Return today's date in the PSX home timezone (Asia/Karachi)."""
    if now is None:
        now = datetime.now(KARACHI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KARACHI)
    return now.astimezone(KARACHI).date()


def is_weekend(day: date) -> bool:
    """PSX is closed on Saturday (5) and Sunday (6)."""
    return day.weekday() >= 5


def build_url(base: str, day: date) -> str:
    """Build the PSX download URL for a given date."""
    return f"{base}/{day.isoformat()}{DOWNLOAD_SUFFIX}"


def marker_path(marker_dir: Path, day: date) -> Path:
    """Path of the done-marker for a given date."""
    return marker_dir / f"{day.isoformat()}{MARKER_SUFFIX}"


def download(
    url: str,
    *,
    session: requests.Session | None = None,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
) -> requests.Response:
    """GET *url*, retrying transport-level errors with backoff.

    Non-2xx responses are returned as-is; only ``requests.RequestException``
    (connection drops, timeouts, ...) is retried.
    """
    session = session or requests.Session()
    last_error: requests.RequestException | None = None
    for attempt in range(1, retries + 1):
        try:
            return session.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(RETRY_BACKOFF * attempt)
    assert last_error is not None
    raise last_error


def is_valid_zip(content: bytes) -> bool:
    """Return True if *content* looks like a readable ZIP archive."""
    try:
        return zipfile.is_zipfile(BytesIO(content))
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def safe_member_path(member: str) -> PurePosixPath:
    """Validate a ZIP member name and return its safe relative path.

    Absolute paths, drive letters and any ``..`` component are rejected
    (zip-slip / path traversal). Backslashes are treated as separators
    because some ZIP producers use them even in ``/``-separated archives.
    """
    raw = member.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ZipSlipError(f"absolute or drive-letter path in archive: {member!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ZipSlipError(f"path traversal in archive: {member!r}")
    return path


def extract_safely(content: bytes, dest_dir: Path) -> list[str]:
    """Extract every file member of an in-memory ZIP into *dest_dir*.

    Member names are validated (zip-slip protection) and nested directories
    are created on demand. Existing files are overwritten. Directory entries
    are skipped. Returns the list of extracted member names.
    """
    dest_dir = dest_dir.resolve()
    extracted: list[str] = []
    with zipfile.ZipFile(BytesIO(content)) as zf:
        for member in zf.namelist():
            if not member or member.endswith("/"):
                continue  # directory entry
            rel = safe_member_path(member)
            if not rel.parts:
                continue
            target = dest_dir.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, open(target, "wb") as target_fh:
                target_fh.write(source.read())
            extracted.append(member)
    return extracted


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (kept separate for testability)."""
    parser = argparse.ArgumentParser(
        prog="psx-summary",
        description=(
            "Download and extract the PSX daily market summary archive "
            "for a given trading date."
        ),
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help=(
            "trading date to fetch (default: today in Asia/Karachi); "
            "explicit dates bypass the weekend check"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if the day was already processed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned action without downloading or writing anything",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            f"directory for downloaded archives "
            f"(default: {DOWNLOAD_FOLDER!r}, relative to the working directory)"
        ),
    )
    parser.add_argument(
        "--marker-dir",
        type=Path,
        default=None,
        help=(
            f"directory for .done markers "
            f"(default: {MARKER_FOLDER!r}, relative to the working directory)"
        ),
    )
    return parser


def run(args: argparse.Namespace, session: requests.Session | None = None) -> int:
    """Execute the download run described by *args*; return an exit code.

    *session* is injected for tests; a real ``requests.Session`` is created
    when omitted.
    """
    day = args.date or market_date()
    dest_dir = args.output_dir or Path(DOWNLOAD_FOLDER)
    marker_dir = args.marker_dir or Path(MARKER_FOLDER)
    marker = marker_path(marker_dir, day)

    # Weekend skip applies to the automatic daily run only; an explicit
    # --date (backfill) is always attempted.
    if is_weekend(day) and args.date is None:
        print("Weekend. Exiting.")
        return EXIT_OK

    if args.dry_run:
        print(f"Date:    {day.isoformat()} ({'weekend' if is_weekend(day) else 'weekday'})")
        print(f"URL:     {build_url(BASE_URL, day)}")
        print(f"Output:  {dest_dir}")
        print(f"Marker:  {marker} ({'exists' if marker.exists() else 'missing'})")
        action = "SKIP (already processed)" if marker.exists() and not args.force else "DOWNLOAD"
        print(f"Action:  {action}")
        return EXIT_OK

    dest_dir.mkdir(parents=True, exist_ok=True)
    marker_dir.mkdir(parents=True, exist_ok=True)

    if marker.exists() and not args.force:
        print("Already processed.")
        return EXIT_OK

    url = build_url(BASE_URL, day)
    print("Checking")
    print(url)

    try:
        response = download(url, session=session)
    except requests.RequestException as exc:
        print(f"Download failed after {DEFAULT_RETRIES} attempts: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print("Status:", response.status_code)

    if response.status_code != 200:
        print("PSX file not uploaded yet.")
        return EXIT_NOT_UPLOADED

    content = response.content
    if not is_valid_zip(content):
        print("Downloaded file is not a valid ZIP archive.", file=sys.stderr)
        return EXIT_ERROR

    archive_path = dest_dir / f"{day.isoformat()}{DOWNLOAD_SUFFIX}"
    with open(archive_path, "wb") as target_fh:
        target_fh.write(content)
    print(f"Downloaded: {archive_path}")

    print("\nZIP Contents:")
    try:
        extracted = extract_safely(content, dest_dir)
    except ZipSlipError as exc:
        archive_path.unlink(missing_ok=True)
        print(f"Unsafe archive rejected: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError) as exc:
        archive_path.unlink(missing_ok=True)
        print(f"Extraction failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_ERROR

    for name in extracted:
        print(f"  - {name}")
    print("\nExtraction completed.")

    marker.write_text("done")
    print("\nCompleted Successfully.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
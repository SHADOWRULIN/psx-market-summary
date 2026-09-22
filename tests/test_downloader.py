"""Offline tests for the PSX market summary downloader.

All network and clock dependencies are injected or monkeypatched, so the
suite runs anywhere without touching the PSX server.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import requests

import downloader

# Keep retry sleeps out of the tests.
downloader.RETRY_BACKOFF = 0

WEEKDAY = date(2026, 9, 21)  # Monday
SATURDAY = date(2026, 9, 19)
SUNDAY = date(2026, 9, 20)

LISTING = b"SYMBOL,CLOSE\nKEL,1.23\nMARI,456.78\n"


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class FakeSession:
    """Records calls; raises ConnectionError while *failures* > 0."""

    def __init__(self, responses=None, failures: int = 0):
        self.responses = list(responses or [])
        self.failures = failures
        self.urls: list[str] = []
        self.headers_seen: list[dict] = []
        self.timeouts: list = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        self.headers_seen.append(headers)
        self.timeouts.append(timeout)
        if self.failures > 0:
            self.failures -= 1
            raise requests.ConnectionError("simulated network failure")
        return self.responses.pop(0) if self.responses else FakeResponse()


def make_args(**overrides) -> argparse.Namespace:
    parser = downloader.build_parser()
    argv = []
    for key, value in overrides.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is False or value is None:
            pass
        else:
            argv.extend([flag, str(value)])
    return parser.parse_args(argv)


# --------------------------------------------------------------------------
# Clock / URL helpers
# --------------------------------------------------------------------------


def test_is_weekend():
    assert not downloader.is_weekend(WEEKDAY)
    assert downloader.is_weekend(SATURDAY)
    assert downloader.is_weekend(SUNDAY)


def test_market_date_aware_now():
    now = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
    # 23:30 UTC Monday == 04:30 Tuesday in Karachi (UTC+5).
    assert downloader.market_date(now) == date(2026, 9, 22)


def test_market_date_naive_now():
    now = datetime(2026, 9, 21, 10, 0)
    assert downloader.market_date(now) == WEEKDAY


def test_build_url():
    assert downloader.build_url("https://dps.psx.com.pk/download/mkt_summary", WEEKDAY) == (
        "https://dps.psx.com.pk/download/mkt_summary/2026-09-21.Z"
    )


def test_marker_path():
    assert downloader.marker_path(Path("processed"), WEEKDAY) == Path(
        "processed/2026-09-21.done"
    )


# --------------------------------------------------------------------------
# Zip-slip protection
# --------------------------------------------------------------------------


def test_safe_member_path_flat():
    p = downloader.safe_member_path("closing11.lis")
    assert p.parts == ("closing11.lis",)


def test_safe_member_path_nested():
    p = downloader.safe_member_path("sub/dir/file.csv")
    assert p.parts == ("sub", "dir", "file.csv")


def test_safe_member_path_backslash_separator():
    p = downloader.safe_member_path("sub\\file.csv")
    assert p.parts == ("sub", "file.csv")


@pytest.mark.parametrize(
    "member",
    [
        "../evil.txt",
        "a/../../evil.txt",
        "/etc/passwd",
        "C:/windows/evil.txt",
        "..\\evil.txt",
    ],
)
def test_safe_member_path_rejects_unsafe(member):
    with pytest.raises(downloader.ZipSlipError):
        downloader.safe_member_path(member)


def test_extract_safely_basic(tmp_path):
    content = make_zip({"closing11.lis": LISTING})
    extracted = downloader.extract_safely(content, tmp_path)
    assert extracted == ["closing11.lis"]
    assert (tmp_path / "closing11.lis").read_bytes() == LISTING


def test_extract_safely_nested_dirs(tmp_path):
    content = make_zip({"a/b/c.txt": b"payload"})
    downloader.extract_safely(content, tmp_path)
    assert (tmp_path / "a" / "b" / "c.txt").read_bytes() == b"payload"


def test_extract_safely_skips_directory_entries(tmp_path):
    content = make_zip({"sub/": b"", "sub/file.txt": b"y"})
    extracted = downloader.extract_safely(content, tmp_path)
    assert extracted == ["sub/file.txt"]
    assert (tmp_path / "sub" / "file.txt").read_bytes() == b"y"


def test_extract_safely_rejects_traversal(tmp_path):
    content = make_zip({"../evil.txt": b"x"})
    with pytest.raises(downloader.ZipSlipError):
        downloader.extract_safely(content, tmp_path)
    # Nothing may have escaped the destination directory.
    assert not (tmp_path.parent / "evil.txt").exists()
    assert list(tmp_path.iterdir()) == []


def test_extract_safely_rejects_absolute(tmp_path):
    content = make_zip({"/tmp/evil.txt": b"x"})
    with pytest.raises(downloader.ZipSlipError):
        downloader.extract_safely(content, tmp_path)


def test_extract_safely_overwrites_existing(tmp_path):
    target = tmp_path / "closing11.lis"
    target.write_bytes(b"old")
    downloader.extract_safely(make_zip({"closing11.lis": LISTING}), tmp_path)
    assert target.read_bytes() == LISTING


# --------------------------------------------------------------------------
# download() retry behaviour
# --------------------------------------------------------------------------


def test_download_sends_headers_and_timeout():
    session = FakeSession([FakeResponse(200, b"ok")])
    resp = downloader.download("http://example.test/x.Z", session=session, timeout=42)
    assert resp.status_code == 200
    assert session.urls == ["http://example.test/x.Z"]
    assert session.headers_seen[0]["User-Agent"].startswith("Mozilla/5.0")
    assert session.timeouts == [42]


def test_download_retries_then_succeeds():
    session = FakeSession([FakeResponse(200, b"ok")], failures=2)
    resp = downloader.download("http://example.test/x.Z", session=session)
    assert resp.status_code == 200
    assert len(session.urls) == 3  # two failures + final success


def test_download_exhausts_retries():
    session = FakeSession([], failures=10)
    with pytest.raises(requests.ConnectionError):
        downloader.download("http://example.test/x.Z", session=session)
    assert len(session.urls) == downloader.DEFAULT_RETRIES


# --------------------------------------------------------------------------
# run() end-to-end (FakeSession, tmp dirs)
# --------------------------------------------------------------------------


def test_run_weekend_skips(monkeypatch):
    monkeypatch.setattr(downloader, "market_date", lambda: SATURDAY)
    session = FakeSession()
    code = downloader.run(
        make_args(output_dir="/tmp/x/out", marker_dir="/tmp/x/mark"),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert session.urls == []  # no network call at all


def test_run_explicit_weekend_date_bypasses_check():
    session = FakeSession([FakeResponse(404)])
    code = downloader.run(
        make_args(date="2026-09-19", output_dir="/tmp/x/out", marker_dir="/tmp/x/mark"),
        session=session,
    )
    assert code == downloader.EXIT_NOT_UPLOADED
    assert session.urls[0].endswith("2026-09-19.Z")


def test_run_downloads_extracts_and_marks(tmp_path):
    session = FakeSession([FakeResponse(200, make_zip({"closing11.lis": LISTING}))])
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert (tmp_path / "data" / "2026-09-21.Z").exists()
    assert (tmp_path / "data" / "closing11.lis").read_bytes() == LISTING
    assert (tmp_path / "markers" / "2026-09-21.done").read_text() == "done"


def test_run_skips_when_marker_exists(tmp_path):
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    (marker_dir / "2026-09-21.done").write_text("done")
    session = FakeSession()
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=marker_dir,
        ),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert session.urls == []


def test_run_force_redownloads(tmp_path):
    marker_dir = tmp_path / "markers"
    (marker_dir).mkdir()
    (marker_dir / "2026-09-21.done").write_text("done")
    session = FakeSession([FakeResponse(200, make_zip({"closing11.lis": LISTING}))])
    code = downloader.run(
        make_args(
            date="2026-09-21",
            force=True,
            output_dir=tmp_path / "data",
            marker_dir=marker_dir,
        ),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert len(session.urls) == 1


def test_run_not_uploaded_returns_exit_2(tmp_path):
    session = FakeSession([FakeResponse(404)])
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_NOT_UPLOADED
    assert list((tmp_path / "data").iterdir()) == []  # nothing saved


def test_run_invalid_zip(tmp_path):
    session = FakeSession([FakeResponse(200, b"<html>oops, not a zip</html>")])
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_ERROR
    assert list((tmp_path / "data").iterdir()) == []
    assert list((tmp_path / "markers").iterdir()) == []


def test_run_unsafe_archive_rejected(tmp_path):
    evil = make_zip({"../escaped.txt": b"x"})
    session = FakeSession([FakeResponse(200, evil)])
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_ERROR
    assert not (tmp_path / "escaped.txt").exists()  # zip-slip blocked
    assert list((tmp_path / "data").iterdir()) == []  # corrupt archive removed
    assert list((tmp_path / "markers").iterdir()) == []


def test_run_network_failure(tmp_path):
    session = FakeSession([], failures=10)
    code = downloader.run(
        make_args(
            date="2026-09-21",
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_ERROR
    assert not (tmp_path / "data").exists() or list((tmp_path / "data").iterdir()) == []


def test_run_dry_run_writes_nothing(tmp_path):
    session = FakeSession()
    code = downloader.run(
        make_args(
            date="2026-09-21",
            dry_run=True,
            output_dir=tmp_path / "data",
            marker_dir=tmp_path / "markers",
        ),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert session.urls == []
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "markers").exists()


def test_run_dry_run_reports_existing_marker(tmp_path, capsys):
    session = FakeSession()
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    (marker_dir / "2026-09-21.done").write_text("done")
    code = downloader.run(
        make_args(
            date="2026-09-21",
            dry_run=True,
            output_dir=tmp_path / "data",
            marker_dir=marker_dir,
        ),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert "SKIP (already processed)" in capsys.readouterr().out


def test_run_uses_cwd_defaults(tmp_path, monkeypatch):
    """The workflow runs ``python downloader.py`` from the repo root, so the
    default output/marker dirs must resolve relative to the CWD."""
    monkeypatch.chdir(tmp_path)
    session = FakeSession([FakeResponse(200, make_zip({"closing11.lis": LISTING}))])
    code = downloader.run(
        make_args(date="2026-09-21"),
        session=session,
    )
    assert code == downloader.EXIT_OK
    assert (tmp_path / "data" / "2026-09-21.Z").exists()
    assert (tmp_path / "processed" / "2026-09-21.done").exists()


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------


def test_parser_defaults():
    args = downloader.build_parser().parse_args([])
    assert args.date is None
    assert args.force is False
    assert args.dry_run is False
    assert args.output_dir is None
    assert args.marker_dir is None


def test_parser_parses_date():
    args = downloader.build_parser().parse_args(["--date", "2026-09-21"])
    assert args.date == WEEKDAY


def test_parser_rejects_bad_date():
    with pytest.raises(SystemExit):
        downloader.build_parser().parse_args(["--date", "not-a-date"])


def test_main_returns_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "market_date", lambda: WEEKDAY)
    monkeypatch.chdir(tmp_path)
    assert downloader.main(["--dry-run"]) == downloader.EXIT_OK
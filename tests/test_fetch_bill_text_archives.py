"""Tests for fetch_bill_text_archives.py download integrity (issue #212).

Hermetic: synthetic in-memory ZIPs served through respx, no network. Chunked
responses (no content-length) are modelled the way httpx emits them -- an
iterator body -- because that is exactly the case where the byte-count check
cannot fire and the archive's own structure is the only completeness signal.
"""

from __future__ import annotations

import io
import re
import shlex
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from fetch_bill_text_archives import download_zip
from fetch_bill_text_archives import main as fetch_bill_text_archives_main
from fetch_govinfo import sessions_for_congress
from shared.bill_types import BILL_TYPES

ARCHIVE_URL = "https://www.govinfo.gov/bulkdata/BILLS/999/1/hr/BILLS-999-1-hr.zip"


def _bills_zip_bytes() -> bytes:
    """One well-formed BILLS archive ZIP, as govinfo serves it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "BILLS-999hr1ih.xml",
            b"<bill><congress>999</congress><type>HR</type><number>1</number></bill>",
        )
    return buf.getvalue()


def _chunked(body: bytes) -> httpx.Response:
    """Response with an iterator body: transfer-encoding chunked, no content-length."""
    return httpx.Response(200, content=iter([body]))


def _temp_path(dest: Path) -> Path:
    """The in-progress download path, as download_zip derives it inline."""
    return dest.with_suffix(dest.suffix + ".part")


def run_fetch_bill_text_archives(command: str, tmp_path: Path) -> None:
    """Run the CLI command string and ensure --zip-dir and --out-dir point at tmp_path."""
    args = shlex.split(command)
    # Add explicit zip and out dirs so tests remain hermetic and predictable.
    args += ["--zip-dir", str(tmp_path), "--out-dir", str(tmp_path)]
    return fetch_bill_text_archives_main(args)


def assert_files(folder: Path, files: set[str] | list[str]) -> None:
    """Assert the folder contains exactly the given filenames."""
    __tracebackhide__ = True
    actual = {path.name for path in folder.iterdir()}
    expected = set(files)
    if actual != expected:
        extra = actual - expected
        missing = expected - actual
        raise AssertionError(
            "\n".join(
                filter(
                    None,
                    [
                        f"Unexpected file contents in folder {folder}:",
                        f"expected: {expected}",
                        f"actual: {actual}",
                        f"extra: {extra}" if extra else None,
                        f"missing: {missing}" if missing else None,
                    ],
                )
            )
        )


def assert_message_contains_strings(message: str, expected_strings: list[str]) -> None:
    """Assert each expected string appears in message."""
    __tracebackhide__ = True
    for part in expected_strings:
        assert part in message, f"Missing '{part!r}' in message: {message}"


def mock_http_requests(
    url: re.Pattern[str] = re.compile(".*"),
    status_code: int = 200,
    content: bytes | list[bytes] = b"",
    headers: dict[str, str] | None = None,
) -> respx.Route:
    """Mock matching GET requests with one response."""
    return respx.get(url).mock(return_value=httpx.Response(status_code, headers=headers, content=content))


def archive_bytes(members: dict[str, bytes] | None = None) -> bytes:
    """Build a well-formed ZIP archive payload from members."""
    members = members or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for member, body in members.items():
            zf.writestr(member, body)
    return buf.getvalue()


EMPTY_ZIP_BYTES = archive_bytes()


class TestDownloadZip:
    @respx.mock
    def test_truncated_body_without_content_length_is_not_committed(self, tmp_path):
        """A short read on a chunked response must fail, not cache a partial archive (#212).

        Without content-length the byte-count check cannot fire, so before this
        guard the half-archive was committed to dest and every later run skipped
        re-download because dest existed.
        """
        full = _bills_zip_bytes()
        respx.get(ARCHIVE_URL).mock(return_value=_chunked(full[: len(full) // 2]))
        dest = tmp_path / "BILLS-999-1-hr.zip"

        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPError):
                download_zip(client, ARCHIVE_URL, dest)

        assert not dest.exists()
        assert not _temp_path(dest).exists()

    @respx.mock
    def test_healthy_body_without_content_length_is_committed(self, tmp_path):
        """Chunked transfer encoding is normal, not an error: a complete archive still lands."""
        full = _bills_zip_bytes()
        respx.get(ARCHIVE_URL).mock(return_value=_chunked(full))
        dest = tmp_path / "BILLS-999-1-hr.zip"

        with httpx.Client() as client:
            assert download_zip(client, ARCHIVE_URL, dest) is True

        assert dest.read_bytes() == full
        assert not _temp_path(dest).exists()
        with zipfile.ZipFile(dest) as zf:
            assert zf.namelist() == ["BILLS-999hr1ih.xml"]

    @respx.mock
    def test_empty_body_without_content_length_is_not_committed(self, tmp_path):
        """A zero-byte chunked response is a failed download, not an empty archive."""
        respx.get(ARCHIVE_URL).mock(return_value=_chunked(b""))
        dest = tmp_path / "BILLS-999-1-hr.zip"

        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPError):
                download_zip(client, ARCHIVE_URL, dest)

        assert not dest.exists()

    @respx.mock
    def test_short_read_against_content_length_still_raises(self, tmp_path):
        """The header-present check keeps its behavior: fewer bytes than promised fails."""
        full = _bills_zip_bytes()
        respx.get(ARCHIVE_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"content-length": str(len(full))},
                content=iter([full[: len(full) // 2]]),
            )
        )
        dest = tmp_path / "BILLS-999-1-hr.zip"

        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPError, match="Incomplete"):
                download_zip(client, ARCHIVE_URL, dest)

        assert not dest.exists()

    @respx.mock
    def test_healthy_body_with_content_length_is_committed(self, tmp_path):
        """The common path -- server sends content-length and the full archive -- is unchanged."""
        full = _bills_zip_bytes()
        respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, content=full))
        dest = tmp_path / "BILLS-999-1-hr.zip"

        with httpx.Client() as client:
            assert download_zip(client, ARCHIVE_URL, dest) is True

        assert dest.read_bytes() == full

    @respx.mock
    def test_download_archives_logs_saved_archives(self, tmp_path, capsys):
        """Each successful download produces a saved line in the stderr log."""
        mock_http_requests(content=EMPTY_ZIP_BYTES)

        run_fetch_bill_text_archives(
            "--from-congress 119 --to-congress 119 --types hr --download-only",
            tmp_path,
        )
        out, err = capsys.readouterr()

        assert_message_contains_strings(
            err,
            [
                "BILLS-119-1-hr.zip",
                "BILLS-119-2-hr.zip",
            ],
        )

    @respx.mock
    def test_download_archives_error_path_logs_failed(self, tmp_path, capsys):
        """A server error during download produces a FAILED line and no file on disk."""
        mock_http_requests(status_code=500)

        run_fetch_bill_text_archives(
            "--from-congress 119 --to-congress 119 --types hr --download-only",
            tmp_path,
        )
        out, err = capsys.readouterr()

        # Sample output from a real run with network disconnected:
        # 1/2: BILLS-119-1-hr.zip
        #   https://www.govinfo.gov/bulkdata/BILLS/119/1/hr/BILLS-119-1-hr.zip
        # 1/2: FAILED BILLS-119-1-hr.zip: [Errno 8] nodename nor servname provided, or not known
        # 2/2: BILLS-119-2-hr.zip
        #   https://www.govinfo.gov/bulkdata/BILLS/119/2/hr/BILLS-119-2-hr.zip
        # 2/2: FAILED BILLS-119-2-hr.zip: [Errno 8] nodename nor servname provided, or not known
        #   version-count histogram (versions -> #bills): {}
        # convert stats: {'bills_seen': 0}
        assert_message_contains_strings(
            err,
            [
                "FAILED BILLS-119-1-hr.zip",
                "FAILED BILLS-119-2-hr.zip",
            ],
        )


class TestBillTypes:
    @respx.mock
    def test_happy_path_downloads_for_case_insensitive_bill_type(self, tmp_path):
        mock_http_requests(content=EMPTY_ZIP_BYTES)
        run_fetch_bill_text_archives(
            "--from-congress 119 --to-congress 119 --types HR HRes --download-only",
            tmp_path,
        )
        assert_files(
            tmp_path,
            {
                "BILLS-119-1-hr.zip",
                "BILLS-119-2-hr.zip",
                "BILLS-119-1-hres.zip",
                "BILLS-119-2-hres.zip",
            },
        )

    def test_reports_invalid_bill_type(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run_fetch_bill_text_archives(
                "--types not-a-type",
                tmp_path,
            )
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        err = re.sub(r"[.,'\[\]{}]", "", err)
        assert_message_contains_strings(
            err,
            [
                "argument --types: invalid choice: not-a-type",
                "choose from all hr s hjres sjres hres sres hconres sconres",
            ],
        )
        assert_files(tmp_path, [])

    @respx.mock
    def test_no_types_argument_defaults_to_all(self, tmp_path):
        mock_http_requests(content=EMPTY_ZIP_BYTES)
        run_fetch_bill_text_archives(
            "--from-congress 119 --to-congress 119 --download-only",
            tmp_path,
        )
        assert_files(
            tmp_path,
            {
                f"BILLS-119-{session}-{bill_type}.zip"
                for session in sessions_for_congress(119)
                for bill_type in BILL_TYPES
            },
        )

    @respx.mock
    def test_types_containing_all_fetches_all_types(self, tmp_path):
        mock_http_requests(content=EMPTY_ZIP_BYTES)
        run_fetch_bill_text_archives(
            "--from-congress 119 --to-congress 119 --types hr all --download-only",
            tmp_path,
        )
        assert_files(
            tmp_path,
            {
                f"BILLS-119-{session}-{bill_type}.zip"
                for session in sessions_for_congress(119)
                for bill_type in BILL_TYPES
            },
        )

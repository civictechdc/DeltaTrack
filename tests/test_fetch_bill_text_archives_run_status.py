"""Exercise bill-text batch status with offline child processes and mocked HTTP."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

import fetch_bill_text_archives as text_archives
import fetch_govinfo as gi
from shared.bill_types import resolve_bill_types

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "fetch_bill_text_archives.py"
XML = b"<bill>healthy</bill>"
UNREADABLE_XML = b"<bill>unreadable</bill>"
CERTIFICATE_KEYS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
OFFLINE_GUARD = """
import os
import pathlib
import socket

guard = pathlib.Path(os.environ["DELTA_OFFLINE_GUARD_DIR"])
(guard / "loaded").write_text("loaded", encoding="utf-8")

def deny_network(*args, **kwargs):
    (guard / "attempted").write_text("attempted", encoding="utf-8")
    raise OSError("network disabled inside bill-text command test")

socket.create_connection = deny_network
socket.getaddrinfo = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
"""


def _archive(path: Path, members: dict[str, bytes] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in (members or {"BILLS-119hr1ih.xml": XML}).items():
            archive.writestr(name, data)


def _download_tasks(zip_dir: Path) -> list[Path]:
    return [
        zip_dir / f"BILLS-119-{session}-{bill_type}.zip"
        for session in gi.sessions_for_congress(119)
        for bill_type in resolve_bill_types(["hr"])
    ]


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    guard_dir = tmp_path / "offline_guard"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(OFFLINE_GUARD, encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_PROXY") and key not in CERTIFICATE_KEYS
    }
    env["PYTHONPATH"] = str(guard_dir)
    env["DELTA_OFFLINE_GUARD_DIR"] = str(guard_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--from-congress",
            "119",
            "--to-congress",
            "119",
            "--types",
            "hr",
            "--zip-dir",
            str(tmp_path / "zips"),
            "--out-dir",
            str(tmp_path / "output"),
            "--billstatus-dir",
            str(tmp_path / "billstatus"),
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (guard_dir / "loaded").exists()
    return result


def _assert_bill_written(tmp_path: Path, number: int = 1) -> None:
    bill_dir = tmp_path / "output" / f"119-hr-{number}"
    assert bill_dir.is_dir()
    assert {path.name for path in bill_dir.iterdir()} == {"1_introduced-in-house.xml"}
    assert (bill_dir / "1_introduced-in-house.xml").read_bytes() == XML


def _assert_failure_summary(result: subprocess.CompletedProcess[str], **counts: int) -> None:
    errors = [line for line in result.stderr.splitlines() if line.startswith("Error:")]
    summary = ", ".join(f"{key}={count}" for key, count in sorted(counts.items()))
    assert errors == [f"Error: bill text batch incomplete: {summary}"], result.stderr
    assert result.stderr.splitlines()[-1] == errors[0]


@pytest.mark.parametrize("overwrite_args", [(), ("--overwrite-existing",)], ids=["default", "overwrite"])
def test_corrupt_archive_fails_after_writing_healthy_bill(tmp_path, overwrite_args):
    _archive(tmp_path / "zips" / "BILLS-119-2-hr.zip")
    corrupt = tmp_path / "zips" / "BILLS-119-1-hr.zip"
    healthy = (tmp_path / "zips" / "BILLS-119-2-hr.zip").read_bytes()
    corrupt.write_bytes(healthy[: len(healthy) // 2])

    result = _run(tmp_path, "--convert-only", *overwrite_args)

    _assert_bill_written(tmp_path)
    assert result.returncode == 1, result.stderr
    _assert_failure_summary(result, corrupt_zip_skipped=1)


def test_unreadable_member_fails_after_writing_healthy_bill(tmp_path):
    archive = tmp_path / "zips" / "BILLS-119-1-hr.zip"
    _archive(archive, {"BILLS-119hr1ih.xml": UNREADABLE_XML, "BILLS-119hr2ih.xml": XML})
    data = bytearray(archive.read_bytes())
    data[data.index(UNREADABLE_XML)] ^= 1
    archive.write_bytes(data)

    result = _run(tmp_path, "--convert-only")

    _assert_bill_written(tmp_path, 2)
    assert not (tmp_path / "output" / "119-hr-1").exists()
    assert result.returncode == 1, result.stderr
    _assert_failure_summary(result, read_error_skipped=1)


@pytest.mark.parametrize("overwrite_args", [(), ("--overwrite-existing",)], ids=["default", "overwrite"])
def test_healthy_conversion_exits_zero(tmp_path, overwrite_args):
    _archive(tmp_path / "zips" / "BILLS-119-1-hr.zip")

    result = _run(tmp_path, "--convert-only", *overwrite_args)

    _assert_bill_written(tmp_path)
    assert result.returncode == 0, result.stderr


def test_min_versions_filter_alone_exits_zero(tmp_path):
    _archive(tmp_path / "zips" / "BILLS-119-1-hr.zip")

    result = _run(tmp_path, "--convert-only", "--min-versions", "2")

    assert list((tmp_path / "output").iterdir()) == []
    assert "'below_min_versions_skipped': 1" in result.stderr
    assert result.returncode == 0, result.stderr


def test_existing_bill_folder_alone_exits_zero(tmp_path):
    _archive(tmp_path / "zips" / "BILLS-119-1-hr.zip")
    bill_dir = tmp_path / "output" / "119-hr-1"
    bill_dir.mkdir(parents=True)
    marker = bill_dir / "keep.xml"
    marker.write_bytes(b"existing bill")

    result = _run(tmp_path, "--convert-only")

    assert list(bill_dir.iterdir()) == [marker]
    assert marker.read_bytes() == b"existing bill"
    assert "'existing_dir_skipped': 1" in result.stderr
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("download_only", [False, True], ids=["full-run", "download-only"])
def test_download_exception_fails_after_processing_saved_archive(tmp_path, download_only):
    tasks = _download_tasks(tmp_path / "zips")
    _archive(tasks[-1])

    result = _run(tmp_path, *(["--download-only"] if download_only else []))

    assert (tmp_path / "offline_guard" / "attempted").exists()
    assert f"FAILED {tasks[0].name}" in result.stderr
    assert "network disabled inside bill-text command test" in result.stderr
    assert {path.name for path in tasks[-1].parent.iterdir()} == {tasks[-1].name}
    if download_only:
        assert not (tmp_path / "output").exists()
    else:
        _assert_bill_written(tmp_path)
    assert result.returncode == 1, result.stderr
    _assert_failure_summary(result, download_failed=len(tasks) - 1)


def test_multiple_failure_categories_and_counts_preserve_healthy_bill(tmp_path):
    zip_dir = tmp_path / "zips"
    _archive(zip_dir / "BILLS-119-1-hr.zip")
    for session in (1, 2):
        (zip_dir / f"BILLS-118-{session}-s.zip").write_bytes(b"bad zip")

    result = _run(tmp_path, "--types", "hr", "s")

    _assert_bill_written(tmp_path)
    assert result.returncode == 1, result.stderr
    _assert_failure_summary(result, corrupt_zip_skipped=2, download_failed=3)


@pytest.mark.parametrize("download_only", [False, True], ids=["full-run", "download-only"])
def test_existing_archives_need_no_network_and_exit_zero(tmp_path, download_only):
    tasks = _download_tasks(tmp_path / "zips")
    for path in tasks:
        _archive(path)

    result = _run(tmp_path, *(["--download-only"] if download_only else []))

    assert not (tmp_path / "offline_guard" / "attempted").exists()
    assert {path.name for path in tasks[0].parent.iterdir()} == {path.name for path in tasks}
    if download_only:
        assert not (tmp_path / "output").exists()
    else:
        _assert_bill_written(tmp_path)
    assert result.returncode == 0, result.stderr


def _mock_downloads(first_response: httpx.Response | Exception) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("BILLS-119hr1ih.xml", XML)
    payload = buf.getvalue()
    for index, session in enumerate(gi.sessions_for_congress(119)):
        route = respx.get(gi.bills_zip_url(119, session, "hr"))
        if index == 0:
            if isinstance(first_response, Exception):
                route.mock(side_effect=first_response)
            else:
                route.mock(return_value=first_response)
        else:
            route.mock(return_value=httpx.Response(200, content=payload))
    return payload


@respx.mock
def test_download_stage_404_records_no_failure(tmp_path, capsys):
    payload = _mock_downloads(httpx.Response(404))

    result = text_archives.download_archives([119], ["hr"], tmp_path)

    saved = _download_tasks(tmp_path)[1:]
    assert result == (saved, 0)
    assert {path.name for path in tmp_path.iterdir()} == {path.name for path in saved}
    assert all(path.read_bytes() == payload for path in saved)
    assert "no zip (404)" in capsys.readouterr().err


@respx.mock
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.Response(500)],
    ids=["connection-error", "http-500"],
)
def test_download_stage_exception_records_failure_and_continues(tmp_path, capsys, failure):
    payload = _mock_downloads(failure)

    result = text_archives.download_archives([119], ["hr"], tmp_path)

    saved = _download_tasks(tmp_path)[1:]
    assert result == (saved, 1)
    assert {path.name for path in tmp_path.iterdir()} == {path.name for path in saved}
    assert all(path.read_bytes() == payload for path in saved)
    assert "FAILED BILLS-119-1-hr.zip" in capsys.readouterr().err

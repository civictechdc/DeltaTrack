"""Tests for archive cache coherence, download and extract sides (issue #61).

``TestDownloadArchiveZip`` in tests/test_fetch_bill_archives.py covers whether a
partial *body* is committed to disk (#63) -- transfer integrity for one archive. This
file covers the caches on either side of that: which archives a re-run decides to
fetch at all, and turning a cached ZIP into an extracted folder. The failure modes
here are about *reuse* rather than transfer.

The same invariant shape governs both stages. Work is skipped when its output already
exists, which is what keeps a re-run cheap -- but it means the output's mere existence
is taken as proof that the work completed, so anything left behind by a crashed run
would be trusted forever. What makes each skip safe is the failure path removing or
marking its own debris: ``extract_archives`` rmtree's a partial folder, and
``download_archives`` writes an ``.error`` marker and clears it on a later success.
"""

from __future__ import annotations

import io
import struct
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

import fetch_bill_archives
from fetch_bill_archives import (
    MAX_MEMBER_COUNT,
    MAX_UNCOMPRESSED_BYTES,
    ArchivesNotExtracted,
    archive_destination,
    archive_error_path,
    archive_extract_dir,
    archive_url,
    download_archives,
    extract_archive,
    extract_archives,
)


def write_archive(source: Path, name: str, members: dict[str, bytes] | None = None) -> Path:
    """Write a well-formed ZIP named ``{name}.zip`` into source."""
    # `is None`, not `or`: an explicitly empty dict means a zero-member ZIP, and
    # falling back on falsiness would silently write a one-member archive instead.
    if members is None:
        members = {f"{name}-1.xml": b"<billStatus/>"}
    path = source / f"{name}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for member, body in members.items():
            zf.writestr(member, body)
    path.write_bytes(buf.getvalue())
    return path


def write_oversized_archive(source: Path, name: str, declared_sizes: list[int]) -> Path:
    """A ZIP whose central directory DECLARES ``declared_sizes`` uncompressed bytes
    per member while actually holding a few.

    A decompression bomb has exactly this shape at scale: a small archive whose
    members expand enormously on disk. Materializing a real multi-gigabyte expansion
    in a test would spend the disk the ceiling exists to protect, so the declaration
    is patched instead -- and the declaration is what the ceiling reads, because
    ``zipfile`` takes member sizes from the central directory.
    """
    path = source / f"{name}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(len(declared_sizes)):
            zf.writestr(f"{name}-{i}.xml", b"<billStatus/>")
    raw = bytearray(buf.getvalue())
    # Central directory file header: uncompressed size is a 4-byte LE field at +24.
    offset = 0
    for declared in declared_sizes:
        offset = raw.find(b"PK\x01\x02", offset)
        assert offset != -1, "central directory record not found -- the craft is broken"
        struct.pack_into("<I", raw, offset + 24, declared)
        offset += 4
    path.write_bytes(bytes(raw))
    return path


# The timestamp ``archive_bytes`` stamps its member with (#480). It governs that one
# helper, NOT the other ZIP builders above -- those keep the wall clock, because nothing
# compares their bytes and a fixed stamp there would buy nothing. Any fixed value would
# do; the DOS epoch is the floor ZIP can represent and reads as obviously synthetic
# rather than as a plausible "now".
FIXED_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# The mode ``writestr`` puts on a normal file when it builds the ``ZipInfo`` itself.
# Supplying our own ``ZipInfo`` opts out of those defaults, so this is set back
# explicitly rather than left to ``_open_to_write``, which happens to back-fill a zero
# ``external_attr`` with the same value. Relying on that back-fill would make the
# fixture's member mode a side effect of an implementation detail two call levels down.
NORMAL_FILE_EXTERNAL_ATTR = 0o600 << 16


def archive_bytes(name: str = "119-hr") -> bytes:
    """One well-formed BILLSTATUS archive ZIP, as bytes.

    A pure function of ``name``: same argument, same bytes, whenever it is called.
    That is what makes it usable on both sides of a byte-equality assertion, which
    ``TestArchiveBytesIsDeterministic`` pins and the cached-archive test relies on.
    Passing an explicit ``ZipInfo`` is what buys it -- ``writestr`` given a bare
    arcname stamps the member with the wall clock instead (#480).
    """
    info = zipfile.ZipInfo(f"{name}-1.xml", FIXED_ZIP_DATE_TIME)
    info.external_attr = NORMAL_FILE_EXTERNAL_ATTR
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(info, b"<billStatus/>")
    return buf.getvalue()


class TestArchiveBytesIsDeterministic:
    """The fixture's own contract, because a test below leans on it (#480).

    ``test_a_cached_archive_clears_a_stale_marker`` proves the cached archive was not
    rewritten, by comparing the file on disk against a freshly generated one. That
    comparison only means "unchanged" if identical content implies identical bytes.
    It did not: ``writestr`` given a bare arcname stamps the member with
    ``time.localtime()``, which the DOS timestamp field carries at two-second
    granularity -- so the assertion held only while both generations landed in the
    same tick, and failed on a loaded run. The failure surfaced as a cache-coherence
    bug in ``download_archives``, which is the expensive part: the two byte strings
    print identically in the truncated repr, so nothing about the output points at
    the fixture.
    """

    def test_output_does_not_depend_on_the_wall_clock(self, monkeypatch):
        def at(clock, build):
            with monkeypatch.context() as m:
                # A no-op through 3.13, where `writestr` reads `time.localtime`
                # unconditionally. From 3.14 it resolves the stamp through
                # `ZipInfo._for_archive`, which prefers SOURCE_DATE_EPOCH via
                # `time.gmtime` -- so with that variable set in the environment the
                # control below would see one timestamp under both clocks and fail,
                # reporting a broken gate rather than a set env var. Version boundary
                # checked against real 3.13.15 and 3.14.6 interpreters, not inferred.
                m.delenv("SOURCE_DATE_EPOCH", raising=False)
                m.setattr(time, "localtime", lambda *a, **k: time.struct_time(clock))
                return build()

        early = (1999, 12, 31, 23, 59, 58, 4, 365, 0)
        late = (2044, 7, 4, 12, 30, 20, 0, 186, 0)

        # The clock is moved rather than waited on, because generating twice in quick
        # succession passes against the broken helper too -- both calls share a tick,
        # so that check could never fail and would certify nothing.
        #
        # This control does double duty. It is the defect held next to the fix, and it
        # is the only thing proving the patch above actually reaches zipfile: if the
        # clock were not really moving, the assertion below would pass however the
        # helper were written.
        def stamped_by_the_wall_clock() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("119-hr-1.xml", b"<billStatus/>")
            return buf.getvalue()

        assert at(early, stamped_by_the_wall_clock) != at(late, stamped_by_the_wall_clock)

        assert at(early, archive_bytes) == at(late, archive_bytes)

    def test_only_the_timestamp_differs_from_the_old_fixture(self, monkeypatch):
        # Supplying an explicit ``ZipInfo`` opts out of every default ``writestr`` would
        # have filled in, not just the timestamp -- compression, flags, and the member's
        # mode among them. Pinning the OLD construction to the same timestamp isolates
        # that: with the clock held equal, any surviving difference is metadata the
        # repair moved by accident rather than the clock it moved on purpose.
        #
        # Compared over raw bytes rather than an enumerated field list, because the
        # field nobody thought to enumerate is exactly the one that would slip through.
        def old_construction() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("119-hr-1.xml", b"<billStatus/>")
            return buf.getvalue()

        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        monkeypatch.setattr(time, "localtime", lambda *a, **k: time.struct_time(FIXED_ZIP_DATE_TIME + (0, 1, 0)))

        assert old_construction() == archive_bytes()

    def test_the_fixed_timestamp_did_not_cost_the_archive(self):
        # A helper that returned a constant would satisfy the test above perfectly, so
        # the output still has to be a real archive -- otherwise the determinism gate
        # could be met by breaking every test that consumes the fixture.
        with zipfile.ZipFile(io.BytesIO(archive_bytes())) as zf:
            assert zf.namelist() == ["119-hr-1.xml"]
            assert zf.read("119-hr-1.xml") == b"<billStatus/>"
            assert zf.infolist()[0].date_time == FIXED_ZIP_DATE_TIME


class TestDownloadArchivesCacheCoherence:
    """Which archives a re-run decides to fetch, and what a failure leaves behind.

    One congress and one bill type per test, so each exercises the cache decision
    rather than the congress/type enumeration.
    """

    @respx.mock
    def test_existing_archive_is_skipped_without_a_request(self, tmp_path):
        # The skip is what makes a re-run over 112-119 cheap; without it the tool
        # re-downloads hundreds of MB. Asserting the route was never called is the
        # point -- an implementation that fetched and then discarded the body would
        # leave the same files on disk.
        dest = archive_destination(tmp_path, 119, "hr")
        dest.write_bytes(b"cached, not a real zip")
        route = respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(200))

        downloaded = download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        assert downloaded == []  # skipped, so not reported as newly downloaded
        assert not route.called
        assert dest.read_bytes() == b"cached, not a real zip"

    @respx.mock
    def test_successful_download_is_committed_and_reported(self, tmp_path):
        body = archive_bytes()
        respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(200, content=body))

        downloaded = download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        dest = archive_destination(tmp_path, 119, "hr")
        assert downloaded == [dest]
        assert dest.read_bytes() == body
        assert not archive_error_path(tmp_path, 119, "hr").exists()

    @respx.mock
    def test_failed_download_writes_an_error_marker_and_commits_no_archive(self, tmp_path):
        respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(404))

        downloaded = download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        assert downloaded == []
        # No .zip: a committed-but-failed archive would be skipped forever by the
        # test above, permanently caching the failure as if it were data.
        assert not archive_destination(tmp_path, 119, "hr").exists()
        error_path = archive_error_path(tmp_path, 119, "hr")
        assert error_path.exists()
        assert error_path.read_text(encoding="utf-8")  # carries the reason, not empty

    @respx.mock
    def test_a_later_success_clears_a_stale_error_marker(self, tmp_path):
        # The coherence half: without the clear, a marker from a transient outage
        # would keep describing a failure for an archive that is now present, and
        # anything reading markers to decide what is missing would be wrong forever.
        error_path = archive_error_path(tmp_path, 119, "hr")
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text("earlier failure", encoding="utf-8")
        respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(200, content=archive_bytes()))

        download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        assert not error_path.exists()
        assert archive_destination(tmp_path, 119, "hr").exists()

    @respx.mock
    def test_a_failing_archive_does_not_abort_the_batch(self, tmp_path):
        # Tasks are newest-first, so 119 is attempted before 118.
        respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(500))
        respx.get(archive_url(118, "hr")).mock(return_value=httpx.Response(200, content=archive_bytes("118-hr")))

        downloaded = download_archives(118, 119, bill_types=["hr"], destination=tmp_path)

        assert downloaded == [archive_destination(tmp_path, 118, "hr")]
        assert archive_error_path(tmp_path, 119, "hr").exists()
        assert not archive_error_path(tmp_path, 118, "hr").exists()

    @respx.mock
    def test_a_stale_error_marker_alone_does_not_prevent_a_retry(self, tmp_path):
        # A marker records that a download failed, not that it should stop being
        # attempted; only a present .zip suppresses the request. Pinning this keeps a
        # future "skip anything with an .error marker" optimization from silently
        # making transient failures permanent.
        error_path = archive_error_path(tmp_path, 119, "hr")
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text("earlier failure", encoding="utf-8")
        route = respx.get(archive_url(119, "hr")).mock(return_value=httpx.Response(200, content=archive_bytes()))

        download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        assert route.called

    @respx.mock
    def test_a_cached_archive_clears_a_stale_marker(self, tmp_path):
        # #259: the skip-if-exists branch used to return before the marker-clearing
        # line, so an archive that was already present kept an .error marker beside it
        # describing a failure that the archive itself disproves. The two states
        # together are contradictory, so whichever a future consumer reads, it reads a
        # wrong answer for one of them.
        dest = archive_destination(tmp_path, 119, "hr")
        dest.write_bytes(archive_bytes())
        error_path = archive_error_path(tmp_path, 119, "hr")
        error_path.write_text("earlier failure", encoding="utf-8")

        downloaded = download_archives(119, 119, bill_types=["hr"], destination=tmp_path)

        assert not error_path.exists()
        # Still skipped, not re-fetched: clearing the marker must not cost the cache.
        # respx is mocking with no route registered, so any request would raise.
        assert downloaded == []
        assert dest.read_bytes() == archive_bytes()


class TestExtractArchive:
    def test_creates_the_destination_and_writes_members(self, tmp_path):
        archive = write_archive(tmp_path, "119-hr", {"a.xml": b"<a/>", "sub/b.xml": b"<b/>"})
        dest = tmp_path / "out"

        extract_archive(archive, dest)

        assert (dest / "a.xml").read_bytes() == b"<a/>"
        assert (dest / "sub" / "b.xml").read_bytes() == b"<b/>"

    @pytest.mark.parametrize(
        "member",
        [
            pytest.param("../escaped.xml", id="parent-traversal"),
            pytest.param("../../escaped.xml", id="double-parent-traversal"),
            pytest.param("/etc/escaped.xml", id="absolute-path"),
            pytest.param("sub/../../escaped.xml", id="traversal-after-descent"),
        ],
    )
    def test_members_cannot_escape_the_destination_directory(self, tmp_path, member):
        # These archives are third-party input (govinfo bulk data), so a member path
        # is untrusted. zipfile.extractall sanitizes traversal and absolute paths
        # itself, so this passes today and is not a live vulnerability -- it is here
        # because the obvious refactor, replacing extractall with a per-member loop
        # to add filtering or progress output, reintroduces a real escape while every
        # other test in this file stays green. That is not hypothetical: writing such
        # a loop and running this file did land a file outside the test directory.
        #
        # Containment is asserted structurally, against paths under tmp_path only.
        # Asserting on a fixed absolute location instead would make the test depend
        # on global filesystem state -- shared with every other process on the
        # machine, so a stale file from an unrelated run fails it and a concurrent
        # run makes it flaky.
        archive = write_archive(tmp_path, "119-hr", {member: b"<escaped/>"})
        dest = tmp_path / "out"

        extract_archive(archive, dest)

        written = [p for p in dest.rglob("*") if p.is_file()]
        assert written, "nothing extracted, so the containment check proved nothing"
        for path in written:
            assert dest.resolve() in path.resolve().parents
        # Nothing escaped one level up into the directory holding the archive.
        assert not (tmp_path / "escaped.xml").exists()

    def test_archive_declaring_more_than_the_ceiling_is_refused(self, tmp_path):
        # #279: zipfile.extractall applies no bound, so a crafted archive could fill
        # the disk. The refusal must land BEFORE anything is written -- a ceiling
        # checked while extracting would already have spent the disk it protects.
        archive = write_oversized_archive(tmp_path, "119-hr", [MAX_UNCOMPRESSED_BYTES + 1])
        dest = tmp_path / "out"

        with pytest.raises(ValueError, match="uncompressed"):
            extract_archive(archive, dest)

        assert not dest.exists()

    def test_the_ceiling_counts_every_member_not_just_the_largest(self, tmp_path):
        # A bomb split across members would slip a per-member check: each part sits
        # comfortably under the ceiling and only the total is ruinous. Four members at
        # 40% each are individually fine and collectively 1.6x over.
        part = int(MAX_UNCOMPRESSED_BYTES * 0.4)
        archive = write_oversized_archive(tmp_path, "119-hr", [part] * 4)
        dest = tmp_path / "out"

        with pytest.raises(ValueError, match="uncompressed"):
            extract_archive(archive, dest)

        assert not dest.exists()

    def test_an_archive_at_the_ceiling_is_still_extracted(self, tmp_path):
        # The boundary is inclusive, and more to the point the ceiling must not be so
        # eager that it refuses work: a real archive is ~162 MiB expanded, three
        # orders of magnitude under this, and must extract untouched. A test that only
        # ever saw the refusal could not tell a working ceiling from one wired to
        # reject everything.
        archive = write_oversized_archive(tmp_path, "119-hr", [MAX_UNCOMPRESSED_BYTES])
        dest = tmp_path / "out"

        extract_archive(archive, dest)

        assert (dest / "119-hr-0.xml").read_bytes() == b"<billStatus/>"

    def test_archive_with_more_members_than_the_ceiling_is_refused(self, tmp_path, monkeypatch):
        # #306: the byte ceiling sums declared uncompressed sizes, so an archive of
        # empty members declares zero bytes and sails past it no matter how many
        # members it holds -- 200,000 empty files weigh nothing yet exhaust inodes.
        # The member ceiling is what catches it. Members are empty on purpose (declared
        # size 0), so this archive passes the byte ceiling and only the count refuses
        # it, proving the gap the issue describes is actually closed. The ceiling is
        # lowered rather than materializing 100,001 members, whose sole cost is build
        # time -- the check reads len(infolist()), which needs that many real central
        # directory records, so the real bound cannot be faked cheaply the way the byte
        # bomb's declared sizes can.
        monkeypatch.setattr(fetch_bill_archives, "MAX_MEMBER_COUNT", 3)
        archive = write_archive(tmp_path, "119-hr", {f"119-hr-{i}.xml": b"" for i in range(4)})
        dest = tmp_path / "out"

        with pytest.raises(ValueError, match="4 members, over the 3 ceiling"):
            extract_archive(archive, dest)

        assert not dest.exists()

    def test_an_archive_at_the_member_ceiling_is_still_extracted(self, tmp_path, monkeypatch):
        # The boundary is inclusive, and the point mirrors the byte ceiling's: a bound
        # wired to reject everything would pass a refusal test while breaking real
        # extraction. A real archive is ~10,564 members, an order of magnitude under
        # the true ceiling, and must extract untouched.
        monkeypatch.setattr(fetch_bill_archives, "MAX_MEMBER_COUNT", 3)
        archive = write_archive(tmp_path, "119-hr", {f"119-hr-{i}.xml": b"<billStatus/>" for i in range(3)})
        dest = tmp_path / "out"

        extract_archive(archive, dest)

        assert (dest / "119-hr-0.xml").read_bytes() == b"<billStatus/>"
        assert len([p for p in dest.rglob("*") if p.is_file()]) == 3

    def test_member_ceiling_calibration_excludes_the_306_exhaustion_case(self):
        # Two-sided calibration guard (#447): a floor alone only stops the ceiling
        # being lowered too far -- raising it to 50,000,000, or effectively off at
        # 10**12, passed the full suite before this upper bound existed.
        #
        # The floor is the largest known real BILLSTATUS archive, 10,564 members
        # (#300). The ceiling is pinned to the CURRENT production value, 100,000 --
        # NOT merely kept under 200,000, the member count #306 used to demonstrate
        # inode exhaustion. `extract_archive` refuses only on
        # `member_count > MAX_MEMBER_COUNT`, so any ceiling below 200,000, even
        # 199,999, technically still refuses that exact archive, but only after
        # nearly as many inodes are counted as the demonstrated-bad case consumed --
        # eroding the safety margin to nothing while this assertion stayed green.
        # The production comment's own stated rationale for 100,000 is a 2x margin
        # under that threshold ("refusing the 200,000-member archive... by a factor
        # of two"), so the calibration pins that margin explicitly rather than
        # tolerating any widening up to one member short of #306's demonstration.
        #
        # No companion "still extracts a normal archive" test here: that positive
        # control already exists and doesn't need duplicating --
        # test_creates_the_destination_and_writes_members above extracts a small
        # archive under both real, unpatched ceilings.
        assert 10_564 < MAX_MEMBER_COUNT <= 100_000

    def test_byte_ceiling_calibration_has_real_corpus_headroom(self):
        # Companion to the member-ceiling calibration above, for MAX_UNCOMPRESSED_BYTES
        # (#447): before this test, the byte ceiling had no calibration assertion at
        # all, one-sided or otherwise -- every byte-ceiling test builds its fixture
        # FROM the constant (write_oversized_archive(..., [MAX_UNCOMPRESSED_BYTES + 1])),
        # so the fixture rescales with whatever the constant is and can never disagree
        # with it. Widening the constant to 3,900,000,000 -- nearly double its real
        # value -- passed the full suite.
        #
        # The floor is the largest known real archive's expanded size, ~162 MiB
        # (#300). Unlike the member ceiling, there is no single demonstrated-bad byte
        # figure in the tracker to exclude, so the ceiling is set from the constant's
        # own documented margin claim ("2 GiB leaves better than an order of
        # magnitude of headroom" over 162 MiB): 3 GiB is roughly 19x the floor,
        # comfortably inside that claimed order of magnitude while still catching a
        # ceiling widened toward "off".
        #
        # No companion extraction test here either, for the same reason as the member
        # ceiling above: test_creates_the_destination_and_writes_members and
        # test_an_archive_at_the_ceiling_is_still_extracted already cover normal
        # extraction under the real, unpatched byte ceiling.
        assert 162 * 1024**2 < MAX_UNCOMPRESSED_BYTES <= 3 * 1024**3

    def test_raises_on_a_corrupt_archive(self, tmp_path):
        archive = tmp_path / "119-hr.zip"
        archive.write_bytes(b"not a zip")

        with pytest.raises(zipfile.BadZipFile):
            extract_archive(archive, tmp_path / "out")


class TestExtractArchivesCacheCoherence:
    def test_extracts_each_archive_into_a_folder_named_for_its_stem(self, tmp_path):
        # Written out of alphabetical order so the assertion below tests the sort
        # rather than the order the files happened to be created in.
        write_archive(tmp_path, "119-s")
        write_archive(tmp_path, "119-hr")

        extracted = extract_archives(tmp_path)

        # Compared in order, not sorted: extract_archives sorts its glob, and other
        # tests here rely on that ordering being deterministic (the batch-resilience
        # test needs the corrupt archive to come first). Sorting the actual value
        # would discard the very property those tests lean on, leaving the return
        # order free to follow filesystem order unnoticed.
        assert [p.name for p in extracted] == ["119-hr", "119-s"]
        assert (tmp_path / "119-hr" / "119-hr-1.xml").exists()

    def test_existing_folder_is_skipped_and_left_untouched(self, tmp_path):
        # The skip is keyed on folder existence alone, so a pre-existing folder wins
        # over the archive's actual contents. Pinning it keeps the re-run cheap and
        # documents that the folder, not the ZIP, is the cache.
        write_archive(tmp_path, "119-hr", {"fresh.xml": b"<fresh/>"})
        stale_dir = tmp_path / "119-hr"
        stale_dir.mkdir()
        (stale_dir / "stale.xml").write_bytes(b"<stale/>")

        extracted = extract_archives(tmp_path)

        assert extracted == []  # skipped, so not reported as newly extracted
        assert (stale_dir / "stale.xml").exists()
        assert not (stale_dir / "fresh.xml").exists()

    def test_partial_folder_from_a_failed_extract_is_removed(self, tmp_path):
        # The cleanup is what makes the existence-based skip safe: a folder left
        # behind here would be treated as a complete extraction by every later run,
        # silently serving a truncated corpus.
        corrupt = tmp_path / "119-hr.zip"
        corrupt.write_bytes(b"not a zip")

        # The batch reports what it could not extract rather than returning a shorter
        # list (#325); tests/test_fetch_bill_archives_run_status.py owns that contract.
        # What is pinned here is that reporting it does not leave the debris behind.
        with pytest.raises(ArchivesNotExtracted):
            extract_archives(tmp_path)

        assert not archive_extract_dir(tmp_path, corrupt).exists()

    def test_a_failed_archive_does_not_abort_the_batch(self, tmp_path):
        # Sorted order puts the corrupt archive first, so a bare raise would cost the
        # healthy ones too.
        (tmp_path / "119-aaa.zip").write_bytes(b"not a zip")
        write_archive(tmp_path, "119-zzz")

        # Reported once the batch is over, never in place of it (#325): the healthy
        # archive below is the whole point, and a bare raise would cost it.
        with pytest.raises(ArchivesNotExtracted):
            extract_archives(tmp_path)

        assert (tmp_path / "119-zzz" / "119-zzz-1.xml").exists()
        assert not (tmp_path / "119-aaa").exists()

    def test_only_zip_files_are_considered(self, tmp_path, capsys):
        # The bills directory holds bills.csv and extracted folders alongside the
        # archives, so the glob is what keeps them out. Asserting the extracted list
        # alone would not catch a widened glob: a non-ZIP that gets attempted fails to
        # open and is swallowed by the same except that handles a corrupt archive, so
        # the list comes out identical either way and only the log betrays it.
        write_archive(tmp_path, "119-hr")
        (tmp_path / "notes.txt").write_text("ignore me")
        (tmp_path / "bills.csv").write_text("id\n")

        extracted = extract_archives(tmp_path)

        assert [p.name for p in extracted] == ["119-hr"]
        assert not (tmp_path / "notes").exists()
        err = capsys.readouterr().err
        assert "notes.txt" not in err
        assert "bills.csv" not in err

    def test_zero_member_archive_still_creates_its_folder(self, tmp_path):
        # A zero-member ZIP is structurally valid and is deliberately not treated as a
        # failed download (see _verify_archive_complete). zipfile.extractall does not
        # create the destination when there is nothing to write, so the explicit
        # mkdir in extract_archive is the only thing that does -- and without the
        # folder the run would report an extraction that left no cache entry, so the
        # next run would extract it again instead of skipping.
        write_archive(tmp_path, "119-hr", members={})

        extracted = extract_archives(tmp_path)

        assert [p.name for p in extracted] == ["119-hr"]
        assert (tmp_path / "119-hr").is_dir()
        assert extract_archives(tmp_path) == []  # now a coherent cache entry

    def test_empty_source_directory_yields_nothing(self, tmp_path):
        assert extract_archives(tmp_path) == []

    def test_missing_source_directory_raises(self, tmp_path):
        missing = tmp_path / "nope"
        with pytest.raises(ValueError, match="Source folder does not exist"):
            extract_archives(missing)

    def test_rerun_after_a_successful_extract_is_a_no_op(self, tmp_path):
        # The cache-coherence property stated end to end: extract, then extract again
        # and get nothing new, with the first run's output intact.
        write_archive(tmp_path, "119-hr")

        first = extract_archives(tmp_path)
        second = extract_archives(tmp_path)

        assert [p.name for p in first] == ["119-hr"]
        assert second == []
        assert (tmp_path / "119-hr" / "119-hr-1.xml").exists()

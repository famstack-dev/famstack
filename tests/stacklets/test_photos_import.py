"""Unit tests for photos import year extraction.

Tests year extraction from EXIF metadata and filename patterns. The
exiftool tests create real JPEG files with embedded EXIF dates to catch
regressions in exiftool output format parsing — no mocked output.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent
           / "stacklets" / "photos" / "cli"),
)

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "album_import",
    Path(__file__).resolve().parent.parent.parent
    / "stacklets" / "photos" / "cli" / "import.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_year_from_filename = _mod._year_from_filename
_parse_exiftool_output = _mod._parse_exiftool_output
_extract_years = _mod._extract_years

HAS_EXIFTOOL = bool(__import__("shutil").which("exiftool"))


def _make_jpeg(path, exif_date=None):
    """Create a minimal valid JPEG file, optionally with a DateTimeOriginal tag.

    Builds the EXIF structure by hand — no PIL dependency needed.
    """
    buf = bytearray()
    buf += b'\xff\xd8'  # SOI

    if exif_date:
        date_bytes = exif_date.encode("ascii") + b'\x00'  # null-terminated

        # TIFF header (little-endian)
        tiff = bytearray()
        tiff += b'II'           # little-endian
        tiff += struct.pack('<H', 42)  # magic
        tiff += struct.pack('<I', 8)   # offset to IFD0

        # IFD0 with one entry pointing to ExifIFD
        tiff += struct.pack('<H', 1)  # 1 entry
        # ExifIFD pointer (tag 0x8769)
        exif_ifd_offset_pos = len(tiff) + 8
        tiff += struct.pack('<HHI', 0x8769, 4, 1)  # tag, LONG, count=1
        tiff += struct.pack('<I', 0)  # placeholder for ExifIFD offset
        tiff += struct.pack('<I', 0)  # next IFD = 0

        # ExifIFD
        exif_ifd_offset = len(tiff)
        struct.pack_into('<I', tiff, exif_ifd_offset_pos, exif_ifd_offset)

        tiff += struct.pack('<H', 1)  # 1 entry
        # DateTimeOriginal (0x9003), ASCII
        date_offset = len(tiff) + 12 + 4  # after this entry + next IFD ptr
        tiff += struct.pack('<HHI', 0x9003, 2, len(date_bytes))
        tiff += struct.pack('<I', date_offset)
        tiff += struct.pack('<I', 0)  # next IFD = 0
        tiff += date_bytes

        # APP1 marker
        exif_payload = b'Exif\x00\x00' + bytes(tiff)
        buf += b'\xff\xe1'
        buf += struct.pack('>H', len(exif_payload) + 2)
        buf += exif_payload

    # Minimal SOS + EOI
    buf += b'\xff\xda\x00\x02'
    buf += b'\xff\xd9'

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(buf))


# ── Year from filename ─────────────────────────────────────────────────


class TestYearFromFilename:
    """Extract year from common photo filename patterns."""

    def test_android_camera(self):
        assert _year_from_filename("20181231_205906.jpg") == "2018"

    def test_android_img_prefix(self):
        assert _year_from_filename("IMG_20190101_120000.jpg") == "2019"

    def test_whatsapp(self):
        assert _year_from_filename("IMG-20200412-WA0032.jpg") == "2020"

    def test_samsung_style(self):
        assert _year_from_filename("20170815_134522_HDR.jpg") == "2017"

    def test_google_takeout(self):
        assert _year_from_filename("2016-08-14.jpg") == "2016"

    def test_no_year_generic(self):
        assert _year_from_filename("random.jpg") is None

    def test_no_year_dsc(self):
        assert _year_from_filename("DSC0001.jpg") is None

    def test_no_year_short_number(self):
        assert _year_from_filename("IMG_001.jpg") is None

    def test_year_too_old(self):
        assert _year_from_filename("18901231_120000.jpg") is None

    def test_year_too_future(self):
        assert _year_from_filename("20401231_120000.jpg") is None

    def test_boundary_1990(self):
        assert _year_from_filename("19900101_000000.jpg") == "1990"

    def test_boundary_2039(self):
        assert _year_from_filename("20390101_000000.jpg") == "2039"


# ── Exiftool output parsing (string-level) ─────────────────────────────


class TestParseExiftoolOutput:
    """Parse raw exiftool -DateTimeOriginal -s3 -f batch output."""

    def test_single_file_with_date(self):
        stdout = (
            "======== /tmp/a.jpg\n"
            "2018:12:31 20:59:06\n"
            "    1 image files read\n"
        )
        assert _parse_exiftool_output(stdout) == ["2018:12:31 20:59:06"]

    def test_single_file_no_date(self):
        stdout = (
            "======== /tmp/a.jpg\n"
            "-\n"
            "    1 image files read\n"
        )
        assert _parse_exiftool_output(stdout) == ["-"]

    def test_multiple_files(self):
        stdout = (
            "======== /tmp/a.jpg\n"
            "2018:12:31 20:59:06\n"
            "======== /tmp/b.jpg\n"
            "2019:06:15 10:30:00\n"
            "======== /tmp/c.jpg\n"
            "-\n"
            "    3 image files read\n"
        )
        assert _parse_exiftool_output(stdout) == [
            "2018:12:31 20:59:06",
            "2019:06:15 10:30:00",
            "-",
        ]

    def test_count_matches_files(self):
        files = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
        lines = []
        for f in files:
            lines.append(f"======== /tmp/{f}")
            lines.append("2020:01:01 00:00:00")
        lines.append(f"    {len(files)} image files read")
        values = _parse_exiftool_output("\n".join(lines) + "\n")
        assert len(values) == len(files)


# ── End-to-end year extraction with real EXIF files ────────────────────


@pytest.mark.skipif(not HAS_EXIFTOOL, reason="exiftool not installed")
class TestExtractYearsWithExiftool:
    """Create real JPEG files with EXIF dates and verify _extract_years
    reads them correctly via exiftool."""

    @pytest.fixture
    def source_dir(self, tmp_path):
        return tmp_path / "photos"

    def test_single_file_with_exif(self, source_dir):
        _make_jpeg(source_dir / "photo.jpg", "2018:12:31 20:59:06")
        years = _extract_years(str(source_dir), ["photo.jpg"])
        assert years["photo.jpg"] == "2018"

    def test_multiple_years(self, source_dir):
        _make_jpeg(source_dir / "a.jpg", "2015:03:20 14:22:01")
        _make_jpeg(source_dir / "b.jpg", "2019:08:10 09:00:00")
        _make_jpeg(source_dir / "c.jpg", "2022:01:01 00:00:00")
        years = _extract_years(str(source_dir), ["a.jpg", "b.jpg", "c.jpg"])
        assert years == {"a.jpg": "2015", "b.jpg": "2019", "c.jpg": "2022"}

    def test_no_exif_falls_back_to_filename(self, source_dir):
        _make_jpeg(source_dir / "20180415_120000.jpg")
        years = _extract_years(str(source_dir), ["20180415_120000.jpg"])
        assert years["20180415_120000.jpg"] == "2018"

    def test_no_exif_no_year_in_name(self, source_dir):
        _make_jpeg(source_dir / "random.jpg")
        years = _extract_years(str(source_dir), ["random.jpg"])
        assert years["random.jpg"] is None

    def test_mixed_exif_and_no_exif(self, source_dir):
        _make_jpeg(source_dir / "with_exif.jpg", "2017:06:15 10:30:00")
        _make_jpeg(source_dir / "20190101_120000.jpg")
        _make_jpeg(source_dir / "mystery.jpg")
        rel_paths = ["with_exif.jpg", "20190101_120000.jpg", "mystery.jpg"]
        years = _extract_years(str(source_dir), rel_paths)
        assert years["with_exif.jpg"] == "2017"
        assert years["20190101_120000.jpg"] == "2019"
        assert years["mystery.jpg"] is None

    def test_count_matches_input(self, source_dir):
        """Every input file must have a year entry (even if None)."""
        for i in range(10):
            date = f"20{15 + i % 5}:01:01 00:00:00" if i % 2 == 0 else None
            _make_jpeg(source_dir / f"img_{i:03d}.jpg", date)
        rel_paths = [f"img_{i:03d}.jpg" for i in range(10)]
        years = _extract_years(str(source_dir), rel_paths)
        assert len(years) == 10
        assert set(years.keys()) == set(rel_paths)

    def test_subdirectory_paths(self, source_dir):
        _make_jpeg(source_dir / "2018" / "vacation" / "photo.jpg",
                   "2018:07:20 15:00:00")
        years = _extract_years(str(source_dir),
                               [str(Path("2018") / "vacation" / "photo.jpg")])
        assert years[str(Path("2018") / "vacation" / "photo.jpg")] == "2018"

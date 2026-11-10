"""Offline tests for the dataset downloader (file:// URLs, no network)."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from logad.data import download
from logad.data.download import DatasetSource, download_dataset, extract_member

LOG_TEXT = "- 1117838570 2005.06.03 node 2005-06-03-15.42.50.675872 node RAS KERNEL INFO hello\n"


# Extracting from a zip finds the log file even inside a sub-directory.
def test_extract_member_from_zip(tmp_path: Path) -> None:
    archive = tmp_path / "BGL.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BGL/BGL.log", LOG_TEXT)
    out = extract_member(archive, "BGL.log", tmp_path)
    assert out.read_text() == LOG_TEXT


# Extracting from a tar.gz works the same way.
def test_extract_member_from_tar(tmp_path: Path) -> None:
    source = tmp_path / "BGL.log"
    source.write_text(LOG_TEXT)
    archive = tmp_path / "BGL.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(source, arcname="BGL.log")
    source.unlink()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    assert extract_member(archive, "BGL.log", out_dir).read_text() == LOG_TEXT


# A missing member produces a clear error.
def test_extract_member_missing(tmp_path: Path) -> None:
    archive = tmp_path / "other.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.txt", "nothing here")
    with pytest.raises(FileNotFoundError):
        extract_member(archive, "BGL.log", tmp_path)


# The first mirror fails, the second (a zip) succeeds and is extracted.
def test_download_dataset_falls_back_to_next_mirror(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "BGL.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BGL.log", LOG_TEXT)
    source = DatasetSource(
        urls=((tmp_path / "missing.zip").as_uri(), archive.as_uri()),
        log_filename="BGL.log",
    )
    monkeypatch.setitem(download.SOURCES, "fake", source)

    out = download_dataset("fake", tmp_path / "raw")
    assert out == tmp_path / "raw" / "BGL.log"
    assert out.read_text() == LOG_TEXT


# A plain (non-archive) download is saved under the expected file name.
def test_download_plain_file(tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "remote.log"
    plain.write_text(LOG_TEXT)
    source = DatasetSource(urls=(plain.as_uri(),), log_filename="BGL_2k.log")
    monkeypatch.setitem(download.SOURCES, "fake2k", source)
    out = download_dataset("fake2k", tmp_path / "raw")
    assert out.name == "BGL_2k.log"
    assert out.read_text() == LOG_TEXT


# When every mirror fails, the error explains how to download manually.
def test_download_dataset_reports_all_failures(tmp_path: Path, monkeypatch) -> None:
    source = DatasetSource(urls=((tmp_path / "nope.zip").as_uri(),), log_filename="BGL.log")
    monkeypatch.setitem(download.SOURCES, "broken", source)
    with pytest.raises(RuntimeError, match="manually"):
        download_dataset("broken", tmp_path / "raw")

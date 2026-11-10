"""Downloading the BGL dataset from Loghub.

Two variants are supported:

* ``bgl``     the full dataset (4,747,963 messages, ~700 MB unpacked) from
              Loghub's Zenodo record (Zhu et al., 2023). This is what every
              reported experiment uses.
* ``bgl_2k``  Loghub's 2,000-line sample from GitHub. It is far too small for
              real results but is handy for smoke-testing the whole pipeline
              in a minute on a laptop.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from logad.utils import ensure_dir, get_logger

logger = get_logger(__name__)


# Where a dataset variant can be fetched from and how its log file is named.
@dataclass(frozen=True)
class DatasetSource:
    # Candidate URLs, tried in order until one succeeds.
    urls: tuple[str, ...]
    # Name of the log file inside the download (or the download itself).
    log_filename: str


SOURCES: dict[str, DatasetSource] = {
    "bgl": DatasetSource(
        urls=(
            # Loghub v8 record (2023) and the older v7 record, in that order.
            "https://zenodo.org/records/8196385/files/BGL.zip?download=1",
            "https://zenodo.org/records/3227177/files/BGL.tar.gz?download=1",
        ),
        log_filename="BGL.log",
    ),
    "bgl_2k": DatasetSource(
        urls=("https://raw.githubusercontent.com/logpai/loghub/master/BGL/BGL_2k.log",),
        log_filename="BGL_2k.log",
    ),
}


# Stream `url` to `destination`, showing a progress bar sized from the
# Content-Length header when the server provides one.
def download_file(url: str, destination: Path, timeout: float = 60.0) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "logad-downloader"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or 0) or None
        with (
            destination.open("wb") as out,
            tqdm(total=total, unit="B", unit_scale=True, desc=destination.name) as progress,
        ):
            # Copy in 1 MiB chunks so memory use stays flat for large archives.
            while chunk := response.read(1 << 20):
                out.write(chunk)
                progress.update(len(chunk))
    return destination


# Extract the member called `filename` from a .zip or .tar.gz archive into
# `output_dir` and return its path. Only that one file is written, which also
# protects against archives containing unexpected absolute or "../" paths.
def extract_member(archive: Path, filename: str, output_dir: Path) -> Path:
    target = output_dir / filename
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            matches = [m for m in zf.namelist() if Path(m).name == filename]
            if not matches:
                raise FileNotFoundError(f"{filename} not found inside {archive.name}")
            with zf.open(matches[0]) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return target

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            matches = [m for m in tf.getmembers() if m.isfile() and Path(m.name).name == filename]
            if not matches:
                raise FileNotFoundError(f"{filename} not found inside {archive.name}")
            src = tf.extractfile(matches[0])
            assert src is not None  # isfile() guarantees a readable member
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return target

    raise ValueError(f"{archive} is neither a zip nor a tar archive")


# Download dataset `name` ("bgl" or "bgl_2k") into `output_dir` and return the
# path of the extracted log file. Existing files are reused unless `force`.
def download_dataset(name: str, output_dir: str | Path, force: bool = False) -> Path:
    if name not in SOURCES:
        raise KeyError(f"Unknown dataset {name!r}; choose from {sorted(SOURCES)}")
    source = SOURCES[name]
    output_dir = ensure_dir(output_dir)
    target = output_dir / source.log_filename
    if target.exists() and not force:
        logger.info("%s already exists, skipping download (use --force to re-download)", target)
        return target

    errors: list[str] = []
    for url in source.urls:
        logger.info("Downloading %s", url)
        try:
            # Download into a temporary directory first so a failed or partial
            # download never leaves a truncated log file behind.
            with tempfile.TemporaryDirectory() as tmp:
                downloaded = download_file(url, Path(tmp) / "download")
                if zipfile.is_zipfile(downloaded) or tarfile.is_tarfile(downloaded):
                    extract_member(downloaded, source.log_filename, output_dir)
                else:
                    shutil.move(str(downloaded), target)
            logger.info("Saved %s", target)
            return target
        except Exception as exc:  # noqa: BLE001 - try the next mirror on any failure
            logger.warning("Download from %s failed: %s", url, exc)
            errors.append(f"{url}: {exc}")

    raise RuntimeError(
        "Could not download the dataset from any source:\n  "
        + "\n  ".join(errors)
        + f"\nDownload it manually from https://zenodo.org/records/8196385 and place "
        f"{source.log_filename} in {output_dir}/"
    )

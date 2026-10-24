"""Minimal in-tree build backend for the posterize project.

The project cannot rely on setuptools being preinstalled in the target uv
environment, so this backend builds the small pure-Python wheel needed for the
console-script slice without external build requirements.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

NAME = "posterize"
VERSION = "0.1.0"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"
ROOT = Path(__file__).resolve().parent

METADATA = """Metadata-Version: 2.1
Name: posterize
Version: 0.1.0
Summary: Convert flat-color PNGs into multi-color 3MF files for Bambu Studio
License: MIT
Classifier: License :: OSI Approved :: MIT License
Requires-Python: >=3.14
Requires-Dist: Pillow
Requires-Dist: bambu3mf @ git+https://github.com/gstark/bambu3mf.git
Requires-Dist: Jinja2
Requires-Dist: textual>=8.2.8
"""

WHEEL = """Wheel-Version: 1.0
Generator: posterize-build-backend
Root-Is-Purelib: true
Tag: py3-none-any
"""

ENTRY_POINTS = """[console_scripts]
posterize = posterize.cli:main
"""


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _write_metadata_dir(Path(metadata_directory))


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _write_metadata_dir(Path(metadata_directory))


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    files = [
        (path, path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "posterize").rglob("*"))
        if path.is_file() and path.suffix != ".pyc"
    ]
    return _write_wheel(Path(wheel_directory), files)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    pth_name = f"_{NAME}_editable.pth"
    return _write_wheel(
        Path(wheel_directory),
        [(None, pth_name, f"{ROOT}{os.linesep}".encode("utf-8"))],
    )


def build_sdist(sdist_directory, config_settings=None):
    sdist_directory = Path(sdist_directory)
    sdist_directory.mkdir(parents=True, exist_ok=True)
    archive_name = f"{NAME}-{VERSION}.tar.gz"
    archive_path = sdist_directory / archive_name
    root_name = f"{NAME}-{VERSION}"

    files = [
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "posterize_build_backend.py",
        ROOT / "pyproject.toml",
    ]
    for directory in (ROOT / "posterize", ROOT / "docs"):
        files.extend(
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix != ".pyc"
        )

    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as sdist:
        for path in files:
            _add_sdist_file(sdist, path, f"{root_name}/{path.relative_to(ROOT)}")
        _add_sdist_bytes(
            sdist,
            f"{root_name}/PKG-INFO",
            METADATA.encode("utf-8"),
        )
    return archive_name


def _write_metadata_dir(metadata_root: Path) -> str:
    dist_info = metadata_root / DIST_INFO
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(METADATA, encoding="utf-8")
    (dist_info / "WHEEL").write_text(WHEEL, encoding="utf-8")
    (dist_info / "entry_points.txt").write_text(ENTRY_POINTS, encoding="utf-8")
    return DIST_INFO


def _write_wheel(wheel_directory: Path, files) -> str:
    wheel_directory.mkdir(parents=True, exist_ok=True)
    wheel_name = f"{NAME}-{VERSION}-py3-none-any.whl"
    wheel_path = wheel_directory / wheel_name
    records: list[tuple[str, str, int | str]] = []

    with ZipFile(wheel_path, "w", ZIP_DEFLATED) as wheel:
        for item in files:
            source, archive_name, *payload = item
            data = payload[0] if payload else Path(source).read_bytes()
            wheel.writestr(archive_name, data)
            records.append((archive_name, _hash(data), len(data)))
        for archive_name, data in {
            f"{DIST_INFO}/METADATA": METADATA.encode("utf-8"),
            f"{DIST_INFO}/WHEEL": WHEEL.encode("utf-8"),
            f"{DIST_INFO}/entry_points.txt": ENTRY_POINTS.encode("utf-8"),
        }.items():
            wheel.writestr(archive_name, data)
            records.append((archive_name, _hash(data), len(data)))
        record_name = f"{DIST_INFO}/RECORD"
        record_lines = [f"{path},{digest},{size}\n" for path, digest, size in records]
        record_lines.append(f"{record_name},,\n")
        wheel.writestr(record_name, "".join(record_lines).encode("utf-8"))
    return wheel_name


def _add_sdist_file(sdist: tarfile.TarFile, path: Path, archive_name: str) -> None:
    info = sdist.gettarinfo(path, archive_name)
    with path.open("rb") as file_obj:
        sdist.addfile(info, file_obj)


def _add_sdist_bytes(sdist: tarfile.TarFile, archive_name: str, data: bytes) -> None:
    info = tarfile.TarInfo(archive_name)
    info.size = len(data)
    sdist.addfile(info, io.BytesIO(data))


def _hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"

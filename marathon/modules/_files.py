"""Shared file helpers for safe copy and move operations."""

# IMPORTS
import errno
import hashlib
import os
import shutil
import threading
from pathlib import Path

from marathon.modules._job import check_stop


# CONFIG
checksum_algorithms = {"MD5": "md5", "SHA-256": "sha256"}
chunk_size = 8 * 1024 * 1024


# FUNC
class NameConflict(Exception):
    """Raised when the requested destination name is already occupied."""


class ChecksumMismatch(Exception):
    """Raised when a copied file does not match its source checksum."""


def free_name(directory: Path, name: str, numbered: bool) -> Path:
    """Return an available path, optionally adding the first free number."""
    candidate = directory / name
    if not os.path.lexists(candidate):
        return candidate
    if not numbered:
        raise NameConflict(f"Datei existiert bereits: {candidate}")

    path = Path(name)
    number = 1
    while os.path.lexists(directory / f"{path.stem}_{number}{path.suffix}"):
        number += 1
    return directory / f"{path.stem}_{number}{path.suffix}"


def _file_digest(path: Path, algorithm: str, stop: threading.Event) -> str:
    """Read a file in blocks and return its hexadecimal digest."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as file:
        while True:
            check_stop(stop)
            block = file.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def copy_file(
    source: Path,
    target: Path,
    stop: threading.Event,
    algorithm: str | None = None,
) -> str | None:
    """Copy without overwriting and optionally verify and return its digest."""
    check_stop(stop)
    digest = hashlib.new(algorithm) if algorithm is not None else None
    target_created = False

    try:
        with open(source, "rb") as source_file:
            try:
                target_file = open(target, "xb")
            except FileExistsError as error:
                raise NameConflict(f"Datei existiert bereits: {target}") from error
            target_created = True
            with target_file:
                while True:
                    check_stop(stop)
                    block = source_file.read(chunk_size)
                    if not block:
                        break
                    if digest is not None:
                        digest.update(block)
                    target_file.write(block)
                target_file.flush()
                os.fsync(target_file.fileno())

        try:
            shutil.copystat(source, target)
        except OSError:
            pass  # Metadata copying is best effort.

        if digest is None:
            return None
        source_hex = digest.hexdigest()
        target_hex = _file_digest(target, algorithm, stop)
        if source_hex != target_hex:
            raise ChecksumMismatch(
                f"Checksumme stimmt nicht ({algorithm}): Quelle {source_hex}, Ziel {target_hex}"
            )
        return source_hex
    except BaseException:
        if target_created:
            try:
                os.remove(target)
            except OSError:
                pass
        raise


def _cross_device(error: OSError) -> bool:
    """Return whether an OS error indicates a cross-device rename."""
    return error.errno == errno.EXDEV or getattr(error, "winerror", None) == 17


def move_file(source: Path, target: Path, stop: threading.Event) -> bool:
    """Move a file, copying and deleting its source across filesystems."""
    check_stop(stop)
    if os.path.lexists(target):
        raise NameConflict(f"Datei existiert bereits: {target}")

    try:
        os.rename(source, target)
        return True
    except FileExistsError as error:
        raise NameConflict(f"Datei existiert bereits: {target}") from error
    except OSError as error:
        if not _cross_device(error):
            raise

    copy_file(source, target, stop)
    try:
        os.remove(source)
    except OSError as error:
        raise OSError(f"Kopie liegt in {target}, Quelle konnte nicht gelöscht werden: {error}") from error
    return False

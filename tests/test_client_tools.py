import hashlib
import os
from pathlib import Path

import pytest

from marathon.server.client_tools import tool_file, tool_manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_empty_or_missing_root_returns_empty_list(tmp_path: Path) -> None:
    assert tool_manifest(tmp_path / "empty") == []
    assert tool_manifest(tmp_path / "missing") == []


def test_manifest_lists_sorted_files_and_tools_with_stable_content_versions(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    beta = root / "beta" / "nested"
    alpha = root / "alpha"
    beta.mkdir(parents=True)
    alpha.mkdir(parents=True)
    (root / "loose.txt").write_text("ignored", encoding="utf-8")
    (beta / "z.bin").write_bytes(b"zeta")
    (root / "beta" / "a.txt").write_bytes(b"alpha")
    (alpha / "tool.exe").write_bytes(b"binary")

    first = tool_manifest(root)
    assert [tool["name"] for tool in first] == ["alpha", "beta"]
    alpha_record, beta_record = first
    assert alpha_record["files"] == [{
        "path": "tool.exe",
        "size": 6,
        "sha256": _sha256(alpha / "tool.exe"),
    }]
    assert beta_record["files"] == [
        {"path": "a.txt", "size": 5, "sha256": _sha256(root / "beta" / "a.txt")},
        {"path": "nested/z.bin", "size": 4, "sha256": _sha256(beta / "z.bin")},
    ]
    expected_version = hashlib.sha256(
        "\n".join(f"{item['path']}\0{item['sha256']}" for item in beta_record["files"]).encode("utf-8")
    ).hexdigest()[:16]
    assert beta_record["version"] == expected_version
    assert tool_manifest(root) == first

    (beta / "z.bin").write_bytes(b"changed")
    changed = tool_manifest(root)
    assert changed[1]["version"] != beta_record["version"]
    assert changed[1]["files"][1]["sha256"] == _sha256(beta / "z.bin")


def test_manifest_ignores_invalid_tool_names_and_part_files(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    (root / "Upper").mkdir(parents=True)
    (root / "-leading").mkdir()
    (root / "valid").mkdir()
    (root / "valid" / "download.part").write_bytes(b"partial")
    (root / "valid" / "complete.bin").write_bytes(b"complete")
    (root / "direct.txt").write_text("ignored", encoding="utf-8")

    assert tool_manifest(root) == [{
        "name": "valid",
        "version": hashlib.sha256(
            f"complete.bin\0{_sha256(root / 'valid' / 'complete.bin')}".encode("utf-8")
        ).hexdigest()[:16],
        "files": [{"path": "complete.bin", "size": 8, "sha256": _sha256(root / "valid" / "complete.bin")}],
    }]


def test_manifest_skips_file_and_directory_symlinks_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink behavior is tested on POSIX")
    root = tmp_path / "tools"
    tool = root / "safe"
    tool.mkdir(parents=True)
    (tool / "real.txt").write_text("real", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tool / "linked.txt").symlink_to(outside)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "hidden.txt").write_text("hidden", encoding="utf-8")
    (tool / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    (root / "linked-tool").symlink_to(tool, target_is_directory=True)

    records = tool_manifest(root)
    assert [record["name"] for record in records] == ["safe"]
    assert [item["path"] for item in records[0]["files"]] == ["real.txt"]


def test_manifest_and_tool_file_reject_symlinked_root_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink behavior is tested on POSIX")
    outside = tmp_path / "outside"
    tool = outside / "valid"
    tool.mkdir(parents=True)
    (tool / "payload.bin").write_bytes(b"secret")
    root = tmp_path / "tools"
    root.symlink_to(outside, target_is_directory=True)

    assert tool_manifest(root) == []
    assert tool_file("valid", "payload.bin", root) is None


def test_tool_file_returns_resolved_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    file_path = root / "valid" / "nested" / "tool.bin"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"tool")

    assert tool_file("valid", "nested/tool.bin", root) == file_path.resolve()


@pytest.mark.parametrize(
    ("name", "relative_path"),
    [
        ("Invalid", "file.txt"),
        ("valid", ""),
        ("valid", "/absolute/file.txt"),
        ("valid", "nested/../file.txt"),
        ("valid", r"nested\\file.txt"),
    ],
)
def test_tool_file_rejects_invalid_name_or_path(tmp_path: Path, name: str, relative_path: str) -> None:
    assert tool_file(name, relative_path, tmp_path) is None


def test_tool_file_rejects_outside_symlink_missing_and_part_paths(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    tool = root / "valid"
    tool.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tool / "escape.txt").symlink_to(outside)
    (tool / "download.part").write_text("partial", encoding="utf-8")

    assert tool_file("valid", "escape.txt", root) is None
    assert tool_file("valid", "missing.txt", root) is None
    assert tool_file("valid", "download.part", root) is None

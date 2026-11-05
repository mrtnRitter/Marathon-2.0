from pathlib import Path, PurePosixPath
from typing import Any


class ModuleContext:
    def __init__(self, storages: dict[str, str | Path], default_storage: str | None = None):
        self.storages = {name: Path(root).expanduser().resolve() for name, root in storages.items()}
        self.default_storage = default_storage

    def storage_for(self, clip: dict[str, Any]) -> str:
        name = clip.get("storage")
        if name in self.storages:
            return name
        for file_ref in clip.get("files", []):
            name = file_ref.get("storage")
            if name in self.storages:
                return name
        if self.default_storage in self.storages:
            return self.default_storage
        if len(self.storages) == 1:
            return next(iter(self.storages))
        if not self.storages:
            raise ValueError("No storage roots are configured")
        raise ValueError("Cannot select a storage for this clip")

    @staticmethod
    def job_name(clip: dict[str, Any]) -> str:
        name = str(clip.get("job", ""))
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
            raise ValueError("Clip job must be a single folder name")
        return path.name

    def job_dir(self, clip: dict[str, Any]) -> Path:
        storage = self.storage_for(clip)
        root = self.storages[storage]
        job_path = (root / self.job_name(clip)).resolve()
        if not job_path.is_relative_to(root):
            raise ValueError("Clip job folder escapes its storage root")
        return job_path

    def resolve(self, file_ref: dict[str, Any]) -> Path:
        storage = file_ref.get("storage")
        if storage not in self.storages:
            raise ValueError(f"Unknown storage: {storage}")
        relative = str(file_ref.get("path", "")).replace("\\", "/")
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            raise ValueError("File reference path must be relative to its storage")
        root = self.storages[storage]
        resolved = root.joinpath(*path.parts).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("File reference escapes its storage root")
        return resolved

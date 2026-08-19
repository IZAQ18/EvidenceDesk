from __future__ import annotations

import os
from pathlib import Path


class DocumentStorage:
    """Local original-file storage with generated paths and containment checks."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("Unsafe document storage path")
        return target

    def save(self, tenant_id: str, document_id: str, extension: str, content: bytes) -> str:
        safe_tenant = "".join(char for char in tenant_id if char.isalnum() or char in "-_")[:80]
        safe_extension = extension.lower() if extension.lower() in {".pdf", ".txt", ".md"} else ".bin"
        relative = f"{safe_tenant}/{document_id}{safe_extension}"
        target = self._resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
        return relative

    def path_for(self, relative_path: str) -> Path:
        path = self._resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def delete(self, relative_path: str | None) -> None:
        if not relative_path:
            return
        path = self._resolve(relative_path)
        if path.is_file():
            path.unlink()

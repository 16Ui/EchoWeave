from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PackageManager:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path

    def list(self) -> list[dict[str, Any]]:
        data = self._load_manifest()
        packages = data.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError("manifest packages must be a list")
        normalized = [self._normalize_package(item) for item in packages if isinstance(item, dict)]
        return sorted(normalized, key=lambda item: item["name"])

    def install(self, name: str, version: str | None = None, source: str = "local") -> dict[str, Any]:
        normalized_name = self._validate_name(name)
        normalized_version = self._normalize_optional_string(version)
        normalized_source = self._normalize_optional_string(source) or "local"
        packages = self.list()
        now = datetime.now(timezone.utc).isoformat()

        for item in packages:
            if item["name"] == normalized_name:
                item["version"] = normalized_version
                item["source"] = normalized_source
                item["installed_at"] = now
                self._write_manifest({"packages": packages})
                return item

        package = {
            "name": normalized_name,
            "version": normalized_version,
            "source": normalized_source,
            "installed_at": now,
        }
        packages.append(package)
        packages.sort(key=lambda item: item["name"])
        self._write_manifest({"packages": packages})
        return package

    def remove(self, name: str) -> dict[str, Any] | None:
        normalized_name = self._validate_name(name)
        packages = self.list()
        remaining: list[dict[str, Any]] = []
        removed: dict[str, Any] | None = None
        for item in packages:
            if item["name"] == normalized_name:
                removed = item
                continue
            remaining.append(item)
        if removed is None:
            return None
        self._write_manifest({"packages": remaining})
        return removed

    def _validate_name(self, name: str) -> str:
        text = (name or "").strip()
        if not text:
            raise ValueError("package name is required")
        if not _PACKAGE_NAME_RE.fullmatch(text):
            raise ValueError("package name may only contain letters, numbers, dot, dash, and underscore")
        return text

    def _normalize_optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_package(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": self._validate_name(str(item.get("name", ""))),
            "version": self._normalize_optional_string(item.get("version")),
            "source": self._normalize_optional_string(item.get("source")) or "local",
            "installed_at": self._normalize_optional_string(item.get("installed_at")),
        }

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"packages": []}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("manifest root must be an object")
        packages = data.get("packages")
        if packages is None:
            return {"packages": []}
        if not isinstance(packages, list):
            raise ValueError("manifest packages must be a list")
        return {"packages": packages}

    def _write_manifest(self, data: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.manifest_path)

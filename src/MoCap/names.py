"""User-defined sensor names, keyed by SteamVR serial number."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import yaml

from .steamvr import runtime_directory


def names_path() -> Path:
    return runtime_directory() / "names.yml"


def load_names(path: Path | None = None) -> dict[str, str]:
    path = Path(path) if path is not None else names_path()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("传感器名称文件格式无效。")
    return {
        serial: name.strip()
        for serial, name in data.items()
        if isinstance(serial, str) and serial and isinstance(name, str) and name.strip()
    }


def save_names(names: dict[str, str], path: Path | None = None) -> None:
    path = Path(path) if path is not None else names_path()
    cleaned = {
        serial: name.strip()
        for serial, name in names.items()
        if isinstance(serial, str) and serial and isinstance(name, str) and name.strip()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            yaml.safe_dump(cleaned, output, allow_unicode=True, sort_keys=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

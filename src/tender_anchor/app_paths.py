"""Filesystem boundaries and deterministic data layout helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import AppError
from .normalize import now_shanghai


def _resolve_project_root() -> Path:
    """Find the checkout root for config/data defaults, including wheel installs."""
    cwd = Path.cwd().resolve()
    source_root = Path(__file__).resolve().parents[2]
    for candidate in (cwd, *cwd.parents, source_root):
        if (candidate / "config" / "sources.yaml").is_file():
            return candidate
    return cwd


PROJECT_ROOT = _resolve_project_root()
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "sources.yaml"
DEFAULT_RULES = PROJECT_ROOT / "config" / "rules.yaml"
DEFAULT_DATA = PROJECT_ROOT / "data"
DEMO_DATA = PROJECT_ROOT / "data_demo"
BAD_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RAW_DIRS = ("raw", "raw/list", "attachments", "exports", "reports")


@dataclass(frozen=True)
class DataPaths:
    root: Path
    db_path: Path
    raw: Path
    attachments: Path
    exports: Path
    reports: Path

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_within(base: Path, candidate: Path, *, label: str = "path") -> Path:
    """Resolve a destination and refuse anything outside ``base``."""
    root = base.resolve()
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AppError(
            "PATH_ESCAPE",
            f"{label} 越出数据目录：{candidate}",
            hint="所有运行期输出必须位于所选数据目录内。",
            base=str(root),
            candidate=str(resolved),
        ) from exc
    return resolved


def ensure_layout(data_dir: Path | str) -> DataPaths:
    root = Path(data_dir).expanduser().resolve()
    ensure_dir(root)
    for name in RAW_DIRS:
        ensure_dir(root / name)
    return DataPaths(
        root=root,
        db_path=root / "tender_anchor.sqlite3",
        raw=root / "raw",
        attachments=root / "attachments",
        exports=root / "exports",
        reports=root / "reports",
    )


def safe_filename(name: str) -> str:
    """Return a display filename with traversal and control characters removed."""
    raw = (name or "").strip().replace("　", " ")
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = BAD_FILENAME_RE.sub("_", raw).strip(" .")
    if not raw or raw in {".", ".."}:
        raise AppError(
            "ATTACHMENT_INVALID_NAME",
            "附件文件名为空或不安全",
            hint="附件文件名必须是不含目录分隔符的 basename。",
            provided=name[:120],
        )
    if len(raw) > 120:
        stem, suffix = os.path.splitext(raw)
        raw = stem[: max(1, 120 - len(suffix))] + suffix
    return raw


def atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    ensure_dir(path.parent)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def write_bytes_if_absent(path: Path, data: bytes) -> bool:
    ensure_dir(path.parent)
    if path.exists():
        return False
    atomic_write_bytes(path, data)
    return True


def make_run_id(config_hash: str) -> str:
    stamp = now_shanghai().strftime("%Y%m%dT%H%M%S%f")
    return f"run-{stamp}-{config_hash[:6]}"


def display_path(paths: DataPaths, candidate: Path) -> str:
    try:
        return paths.relative(candidate)
    except ValueError:
        return candidate.name

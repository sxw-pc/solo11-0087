"""Attachment download, safe naming, hashing, and local storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .app_paths import ensure_dir, ensure_within, safe_filename, write_bytes_if_absent
from .errors import AppError, FetchError
from .extract import AttachmentRef
from .fetch import SafeFetcher
from .normalize import sha256_bytes

MIME_TYPES = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass(frozen=True)
class AttachmentFetchResult:
    filename: str
    source_url: str
    sha256: str | None
    size_bytes: int
    stored_path: str | None
    content_type: str
    status: str
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "stored"


class AttachmentStore:
    """Downloads attachments into a confined directory and records failures."""

    def __init__(
        self,
        data_root: Path,
        fetcher: SafeFetcher,
        *,
        max_bytes: int = 524_288,
    ) -> None:
        self.data_root = data_root.resolve()
        self.attachments_dir = ensure_dir(self.data_root / "attachments")
        self.fetcher = fetcher
        self.max_bytes = max_bytes

    def _failed(
        self, ref: AttachmentRef, code: str, filename: str | None = None
    ) -> AttachmentFetchResult:
        return AttachmentFetchResult(
            filename=filename or ref.filename,
            source_url=ref.url,
            sha256=None,
            size_bytes=0,
            stored_path=None,
            content_type="",
            status="failed",
            error_code=code,
        )

    def download(self, notice_id: str, ref: AttachmentRef) -> AttachmentFetchResult:
        try:
            safe_notice = safe_filename(notice_id)
            filename = safe_filename(ref.filename)
        except AppError as exc:
            return self._failed(ref, exc.code)
        try:
            response = self.fetcher.fetch(ref.url)
        except FetchError as exc:
            return self._failed(ref, exc.code, filename)
        if len(response.content) > self.max_bytes:
            return self._failed(ref, "ATTACHMENT_TOO_LARGE", filename)
        digest = sha256_bytes(response.content)
        relative = (
            Path("attachments") / safe_notice / f"{digest[:12]}_{filename}"
        ).as_posix()
        try:
            destination = ensure_within(
                self.data_root, self.data_root / relative, label="attachment"
            )
            ensure_dir(destination.parent)
            write_bytes_if_absent(destination, response.content)
        except (OSError, AppError):
            return self._failed(ref, "ATTACHMENT_WRITE_ERROR", filename)
        extension = Path(filename).suffix.lower()
        content_type = response.content_type.split(";", 1)[0].strip()
        if not content_type or content_type == "application/octet-stream":
            content_type = MIME_TYPES.get(extension, "application/octet-stream")
        return AttachmentFetchResult(
            filename=filename,
            source_url=ref.url,
            sha256=digest,
            size_bytes=len(response.content),
            stored_path=relative,
            content_type=content_type,
            status="stored",
        )

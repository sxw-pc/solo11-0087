from pathlib import Path

from tender_anchor.app_paths import ensure_within
from tender_anchor.attachments import AttachmentStore
from tender_anchor.extract import AttachmentRef
from tender_anchor.fetch import LoopbackPolicy, SafeFetcher


def _store(tmp_path, portal_url):
    policy = LoopbackPolicy(("127.0.0.1", "localhost", "::1"))
    fetcher = SafeFetcher(policy, timeout_seconds=5)
    return AttachmentStore(tmp_path, fetcher, max_bytes=1024 * 1024), fetcher


def test_attachment_is_stored_inside_data_root(tmp_path, portal_url):
    store, fetcher = _store(tmp_path, portal_url)
    result = store.download(
        "TA-2026-0001",
        AttachmentRef("example_terms.txt", portal_url + "/attachments/example_terms.txt"),
    )
    assert result.status == "stored"
    assert result.error_code is None
    assert result.stored_path.startswith("attachments/TA-2026-0001/")
    destination = ensure_within(tmp_path, tmp_path / result.stored_path)
    assert destination.is_file()
    assert result.sha256 and len(result.sha256) == 64
    assert destination.read_bytes().startswith("示例采购文件条款".encode("utf-8"))
    fetcher.close()


def test_missing_attachment_becomes_explainable_failure(tmp_path, portal_url):
    store, fetcher = _store(tmp_path, portal_url)
    result = store.download(
        "TA-2026-0005",
        AttachmentRef("missing_attachment.txt", portal_url + "/attachments/missing_attachment.txt"),
    )
    assert result.status == "failed"
    assert result.error_code == "FETCH_HTTP_ERROR"
    assert result.sha256 is None
    assert result.stored_path is None
    fetcher.close()


def test_traversal_filename_is_confined(tmp_path, portal_url):
    store, fetcher = _store(tmp_path, portal_url)
    result = store.download(
        "TA-2026-0001",
        AttachmentRef("../../escape.txt", portal_url + "/attachments/example_terms.txt"),
    )
    assert result.status == "stored"
    assert result.filename == "escape.txt"
    destination = ensure_within(tmp_path, tmp_path / result.stored_path)
    assert destination.parent == (tmp_path / "attachments" / "TA-2026-0001").resolve()
    assert Path(tmp_path / "escape.txt").exists() is False
    fetcher.close()


def test_external_attachment_is_blocked_before_network(tmp_path):
    store, fetcher = _store(tmp_path, "http://127.0.0.1:5810")
    result = store.download(
        "TA-2026-0001",
        AttachmentRef("remote.txt", "http://203.0.113.10/attachments/remote.txt"),
    )
    assert result.status == "failed"
    assert result.error_code == "FETCH_HOST_BLOCKED"
    assert not (tmp_path / "attachments").exists() or not any(
        (tmp_path / "attachments").rglob("remote.txt")
    )
    fetcher.close()

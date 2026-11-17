"""SQLite schema for tender notices, provenance, and review state."""

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    portal_base TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    notices_seen INTEGER NOT NULL DEFAULT 0,
    notices_inserted INTEGER NOT NULL DEFAULT 0,
    notices_updated INTEGER NOT NULL DEFAULT 0,
    notices_unchanged INTEGER NOT NULL DEFAULT 0,
    attachments_ok INTEGER NOT NULL DEFAULT 0,
    attachments_failed INTEGER NOT NULL DEFAULT 0,
    review_open INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS notices (
    notice_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    kind_label TEXT NOT NULL,
    title TEXT NOT NULL,
    project_code TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    purchaser TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    publish_at TEXT,
    deadline_at TEXT,
    budget_amount TEXT,
    budget_currency TEXT NOT NULL DEFAULT 'CNY',
    award_amount TEXT,
    winner TEXT NOT NULL DEFAULT '',
    related_notice_id TEXT NOT NULL DEFAULT '',
    content_excerpt TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    first_seen_run TEXT NOT NULL,
    last_seen_run TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notice_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(notice_id, source_hash)
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(notice_id, sha256)
);

CREATE TABLE IF NOT EXISTS attachments (
    attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_url TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    stored_path TEXT,
    content_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error_code TEXT,
    fetched_at TEXT NOT NULL,
    updated_run TEXT NOT NULL,
    UNIQUE(notice_id, source_url)
);

CREATE TABLE IF NOT EXISTS corrections (
    correction_notice_id TEXT PRIMARY KEY,
    original_notice_id TEXT NOT NULL,
    published_at TEXT,
    changes_json TEXT NOT NULL DEFAULT '[]',
    source_url TEXT NOT NULL,
    first_seen_run TEXT NOT NULL,
    last_seen_run TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_sources (
    notice_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    raw_value TEXT NOT NULL DEFAULT '',
    normalized_value TEXT NOT NULL DEFAULT '',
    extractor TEXT NOT NULL,
    selector TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(notice_id, field_name)
);

CREATE TABLE IF NOT EXISTS review_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    field_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_run TEXT NOT NULL,
    last_seen_run TEXT NOT NULL,
    resolved_run TEXT,
    UNIQUE(notice_id, rule_code)
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarification_cases (
    case_key TEXT PRIMARY KEY,
    notice_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    questioner TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    case_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    deadline_basis TEXT NOT NULL DEFAULT '',
    deadline_at TEXT NOT NULL DEFAULT '',
    first_seen_run TEXT NOT NULL,
    last_seen_run TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarification_answers (
    case_key TEXT NOT NULL,
    answer_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '',
    term_id TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    redacted_questioner INTEGER NOT NULL DEFAULT 0,
    content_sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY(case_key, answer_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS clarification_access (
    case_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    answer_id TEXT NOT NULL,
    bidder_id TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(case_key, event_id)
);

CREATE TABLE IF NOT EXISTS parity_certifications (
    case_key TEXT NOT NULL,
    contract_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    created_run TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(case_key, contract_mode)
);

CREATE INDEX IF NOT EXISTS idx_notices_kind ON notices(kind);
CREATE INDEX IF NOT EXISTS idx_notices_publish ON notices(publish_at);
CREATE INDEX IF NOT EXISTS idx_attachments_notice ON attachments(notice_id);
CREATE INDEX IF NOT EXISTS idx_corrections_original ON corrections(original_notice_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, severity);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id);
CREATE INDEX IF NOT EXISTS idx_revisions_notice ON notice_revisions(notice_id);
CREATE INDEX IF NOT EXISTS idx_clarification_notice ON clarification_cases(notice_id);
CREATE INDEX IF NOT EXISTS idx_access_bidder ON clarification_access(case_key, bidder_id);
CREATE INDEX IF NOT EXISTS idx_parity_status ON parity_certifications(status);
"""

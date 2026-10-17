"""SQLite persistence: batches, bundle keys, canonical documents and verifier reasons."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    cid TEXT PRIMARY KEY, key_b64url TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    cid TEXT PRIMARY KEY, canonical BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY, cid TEXT NOT NULL UNIQUE, installation_id TEXT NOT NULL,
    period INTEGER NOT NULL, commitment TEXT NOT NULL, tx_hash TEXT NOT NULL, status TEXT NOT NULL,
    risk_score INTEGER NOT NULL, exporter TEXT NOT NULL, exporter_sig TEXT NOT NULL,
    created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reasons (
    reason_hash TEXT PRIMARY KEY, batch_id INTEGER NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per call: safe across FastAPI's worker threads.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---- bundle key + document, written before the transaction is sent
    def put_pending(self, cid: str, key_b64url: str, canonical: bytes) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO keys VALUES (?,?,?)", (cid, key_b64url, time.time()))
            c.execute("INSERT OR REPLACE INTO documents VALUES (?,?)", (cid, canonical))

    def discard_pending(self, cid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM keys WHERE cid=?", (cid,))
            c.execute("DELETE FROM documents WHERE cid=?", (cid,))

    def get_key(self, cid: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT key_b64url FROM keys WHERE cid=?", (cid,)).fetchone()
        return row["key_b64url"] if row else None

    def get_document(self, cid: str) -> bytes | None:
        with self._conn() as c:
            row = c.execute("SELECT canonical FROM documents WHERE cid=?", (cid,)).fetchone()
        return bytes(row["canonical"]) if row else None

    # ---- batches
    def put_batch(self, *, batch_id: int, cid: str, installation_id: str, period: int, commitment: str,
                  tx_hash: str, status: str, risk_score: int, exporter: str, exporter_sig: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO batches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, cid, installation_id, period, commitment, tx_hash, status, risk_score,
                 exporter, exporter_sig, time.time()),
            )

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def batch_by_cid(self, cid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM batches WHERE cid=?", (cid,)).fetchone()
        return dict(row) if row else None

    # ---- verifier reasons (content-addressed: reason_hash = keccak256(text))
    def put_reason(self, reason_hash: str, batch_id: int, text: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO reasons VALUES (?,?,?,?)", (reason_hash, batch_id, text, time.time()))

    def get_reason(self, reason_hash: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT text FROM reasons WHERE reason_hash=?", (reason_hash,)).fetchone()
        return row["text"] if row else None

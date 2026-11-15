"""SQLite storage and FTS5; all caller mutations use one transaction."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
 current_run_id TEXT, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
 run_id TEXT NOT NULL REFERENCES runs(id), number INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(run_id, number)
);
CREATE TABLE IF NOT EXISTS fields (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), category TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classifications (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 document_id TEXT NOT NULL REFERENCES documents(id), run_id TEXT NOT NULL REFERENCES runs(id), payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_document ON reviews(document_id, sequence);
CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(document_id UNINDEXED, content, tokenize='unicode61 remove_diacritics 2');
PRAGMA user_version=1;
'''

class Store:
    def __init__(self, root: Path):
        self.path = root / 'archive.sqlite3'
        with self.connect() as connection:
            version=connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise RuntimeError('UNSUPPORTED_DATABASE_VERSION')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path, timeout=30)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.uname().sysname == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "muse-shroom"


class Store:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "muse-shroom.sqlite3"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id TEXT PRIMARY KEY, request_json TEXT NOT NULL, mode TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 0, incomplete_phase TEXT
            );
            CREATE TABLE IF NOT EXISTS queries (
                search_id TEXT NOT NULL, query TEXT NOT NULL, kind TEXT NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(search_id, query), FOREIGN KEY(search_id) REFERENCES searches(id)
            );
            CREATE TABLE IF NOT EXISTS repositories (
                full_name TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_candidates (
                search_id TEXT NOT NULL, full_name TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(search_id, full_name), FOREIGN KEY(search_id) REFERENCES searches(id)
            );
            CREATE TABLE IF NOT EXISTS star_snapshots (
                full_name TEXT NOT NULL, captured_at TEXT NOT NULL, stars INTEGER NOT NULL,
                PRIMARY KEY(full_name, captured_at)
            );
            CREATE TABLE IF NOT EXISTS assessments (
                search_id TEXT NOT NULL, full_name TEXT NOT NULL, assessment_json TEXT NOT NULL,
                PRIMARY KEY(search_id, full_name)
            );
            CREATE TABLE IF NOT EXISTS rankings (
                search_id TEXT PRIMARY KEY, ranking_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
                relevant INTEGER, interesting INTEGER, too_hard INTEGER,
                note TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY, body_json TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def create_search(self, search_id: str, request: dict[str, Any], mode: str) -> None:
        now = utc_now()
        self.db.execute(
            "INSERT INTO searches VALUES (?, ?, ?, ?, ?, 0, NULL)",
            (search_id, json.dumps(request, ensure_ascii=False), mode, now, now),
        )
        self.db.commit()

    def mark_search(self, search_id: str, *, stale: bool, incomplete_phase: str | None) -> None:
        self.db.execute(
            "UPDATE searches SET stale=?, incomplete_phase=?, updated_at=? WHERE id=?",
            (int(stale), incomplete_phase, utc_now(), search_id),
        )
        self.db.commit()

    def add_query(self, search_id: str, query: str, kind: str, result_count: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO queries VALUES (?, ?, ?, ?)",
            (search_id, query, kind, result_count),
        )
        self.db.commit()

    def save_candidate(self, search_id: str, candidate: dict[str, Any]) -> None:
        full_name = str(candidate["full_name"]).lower()
        now = utc_now()
        payload = json.dumps(candidate, ensure_ascii=False)
        self.db.execute("INSERT OR REPLACE INTO repositories VALUES (?, ?, ?)", (full_name, payload, now))
        self.db.execute("INSERT OR REPLACE INTO search_candidates VALUES (?, ?, ?)", (search_id, full_name, payload))
        self.db.execute(
            "INSERT OR IGNORE INTO star_snapshots VALUES (?, ?, ?)",
            (full_name, now, int(candidate.get("stargazers_count", 0))),
        )
        self.db.commit()

    def load_search(self, search_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown search_id: {search_id}")
        candidates = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload_json FROM search_candidates WHERE search_id=? ORDER BY full_name", (search_id,)
        )]
        return {**dict(row), "request": json.loads(row["request_json"]), "candidates": candidates}

    def get_candidate(self, full_name: str, search_id: str | None = None) -> dict[str, Any] | None:
        if search_id:
            row = self.db.execute(
                "SELECT payload_json FROM search_candidates WHERE search_id=? AND full_name=?",
                (search_id, full_name.lower()),
            ).fetchone()
        else:
            row = self.db.execute("SELECT snapshot_json FROM repositories WHERE full_name=?", (full_name.lower(),)).fetchone()
        return json.loads(row[0]) if row else None

    def save_assessment(self, search_id: str, full_name: str, assessment: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO assessments VALUES (?, ?, ?)",
            (search_id, full_name.lower(), json.dumps(assessment, ensure_ascii=False)),
        )
        self.db.commit()

    def save_ranking(self, search_id: str, ranking: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO rankings VALUES (?, ?, ?)",
            (search_id, json.dumps(ranking, ensure_ascii=False), utc_now()),
        )
        self.db.commit()

    def get_cache(self, key: str) -> tuple[Any, str] | None:
        with self._lock:
            row = self.db.execute("SELECT body_json, fetched_at FROM api_cache WHERE cache_key=?", (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def set_cache(self, key: str, body: Any) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?)",
                (key, json.dumps(body, ensure_ascii=False), utc_now()),
            )
            self.db.commit()

    def star_history(self, full_name: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT captured_at, stars FROM star_snapshots WHERE full_name=? ORDER BY captured_at", (full_name.lower(),)
        )]

    def add_feedback(self, full_name: str, relevant: bool | None, interesting: bool | None,
                     too_hard: bool | None, note: str | None) -> None:
        self.db.execute(
            "INSERT INTO feedback(full_name,relevant,interesting,too_hard,note,created_at) VALUES(?,?,?,?,?,?)",
            (full_name.lower(), relevant, interesting, too_hard, note, utc_now()),
        )
        self.db.commit()

    def feedback_bias(self, full_name: str, topics: list[str] | None = None) -> float:
        rows = self.db.execute(
            "SELECT relevant, interesting, too_hard FROM feedback WHERE full_name=?", (full_name.lower(),)
        ).fetchall()
        values = []
        for row in rows:
            values.append((1 if row[0] else -1 if row[0] is not None else 0) * 0.4)
            values.append((1 if row[1] else -1 if row[1] is not None else 0) * 0.4)
            values.append((-1 if row[2] else 0) * 0.2)
        topic_set = {str(topic).lower() for topic in (topics or [])}
        if topic_set:
            related = self.db.execute(
                "SELECT f.relevant,f.interesting,f.too_hard,r.snapshot_json "
                "FROM feedback f JOIN repositories r ON r.full_name=f.full_name"
            ).fetchall()
            for row in related:
                snapshot_topics = {str(topic).lower() for topic in json.loads(row[3]).get("topics", [])}
                if topic_set & snapshot_topics:
                    values.extend([
                        (1 if row[0] else -1 if row[0] is not None else 0) * .2,
                        (1 if row[1] else -1 if row[1] is not None else 0) * .2,
                        (-1 if row[2] else 0) * .1,
                    ])
        return max(-1.0, min(1.0, sum(values) / max(1, len(rows))))

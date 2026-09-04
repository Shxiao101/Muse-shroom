from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .boundary import boundary_delta
from .iteration import default_session_state


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
            CREATE TABLE IF NOT EXISTS rankings (
                search_id TEXT PRIMARY KEY, ranking_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boundary_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL, stage TEXT NOT NULL,
                boundary_json TEXT NOT NULL, delta_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                iteration INTEGER,
                hypothesis_json TEXT,
                query_summary_json TEXT,
                FOREIGN KEY(search_id) REFERENCES searches(id)
            );
            CREATE TABLE IF NOT EXISTS query_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 0,
                query TEXT NOT NULL, kind TEXT NOT NULL,
                fingerprint TEXT NOT NULL, result_count INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                skip_reason TEXT,
                FOREIGN KEY(search_id) REFERENCES searches(id)
            );
            CREATE TABLE IF NOT EXISTS search_iterations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL, iteration INTEGER NOT NULL,
                event TEXT NOT NULL DEFAULT 'iterate',
                stage TEXT NOT NULL, hypothesis_json TEXT,
                query_summary_json TEXT, stop_reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(search_id) REFERENCES searches(id)
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
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(searches)")}
        if "fingerprint" not in columns:
            self.db.execute("ALTER TABLE searches ADD COLUMN fingerprint TEXT")
        if "session_state_json" not in columns:
            self.db.execute("ALTER TABLE searches ADD COLUMN session_state_json TEXT")
        snapshot_columns = {row[1] for row in self.db.execute("PRAGMA table_info(boundary_snapshots)")}
        if "iteration" not in snapshot_columns:
            self.db.execute("ALTER TABLE boundary_snapshots ADD COLUMN iteration INTEGER")
        if "hypothesis_json" not in snapshot_columns:
            self.db.execute("ALTER TABLE boundary_snapshots ADD COLUMN hypothesis_json TEXT")
        if "query_summary_json" not in snapshot_columns:
            self.db.execute("ALTER TABLE boundary_snapshots ADD COLUMN query_summary_json TEXT")
        if "repos_json" not in snapshot_columns:
            self.db.execute("ALTER TABLE boundary_snapshots ADD COLUMN repos_json TEXT")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_searches_fingerprint ON searches(fingerprint, mode)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_boundary_search ON boundary_snapshots(search_id, id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_query_history_search ON query_history(search_id, id)"
        )
        history_columns = {row[1] for row in self.db.execute("PRAGMA table_info(query_history)")}
        if "skip_reason" not in history_columns:
            self.db.execute("ALTER TABLE query_history ADD COLUMN skip_reason TEXT")
        self._migrate_search_iterations()
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_iterations_search ON search_iterations(search_id, id)"
        )
        self.db.commit()

    def create_search(self, search_id: str, request: dict[str, Any], mode: str,
                      fingerprint: str | None = None) -> None:
        now = utc_now()
        self.db.execute(
            """INSERT INTO searches
               (id, request_json, mode, created_at, updated_at, stale, incomplete_phase, fingerprint)
               VALUES (?, ?, ?, ?, ?, 0, NULL, ?)""",
            (search_id, json.dumps(request, ensure_ascii=False), mode, now, now, fingerprint),
        )
        self.db.commit()

    def find_complete_search(self, fingerprint: str, mode: str) -> str | None:
        row = self.db.execute(
            """SELECT id FROM searches
               WHERE fingerprint=? AND mode=? AND incomplete_phase IS NULL
               ORDER BY updated_at DESC LIMIT 1""",
            (fingerprint, mode),
        ).fetchone()
        return None if row is None else str(row["id"])

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

    def _migrate_search_iterations(self) -> None:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(search_iterations)")}
        if not columns or ("id" in columns and "event" in columns):
            return
        self.db.execute(
            """CREATE TABLE search_iterations_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL, iteration INTEGER NOT NULL,
                event TEXT NOT NULL DEFAULT 'iterate',
                stage TEXT NOT NULL, hypothesis_json TEXT,
                query_summary_json TEXT, stop_reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(search_id) REFERENCES searches(id)
            )"""
        )
        self.db.execute(
            """INSERT INTO search_iterations_new
               (search_id, iteration, event, stage, hypothesis_json, query_summary_json,
                stop_reason, created_at)
               SELECT search_id, iteration,
                      CASE
                        WHEN stop_reason = 'agent_stop' THEN 'stop'
                        WHEN stop_reason IN ('max_iterations', 'query_budget_exhausted') THEN 'refuse'
                        ELSE COALESCE(stage, 'iterate')
                      END,
                      stage, hypothesis_json, query_summary_json, stop_reason, created_at
               FROM search_iterations"""
        )
        self.db.execute("DROP TABLE search_iterations")
        self.db.execute("ALTER TABLE search_iterations_new RENAME TO search_iterations")

    def add_query_history(self, search_id: str, query: str, kind: str, result_count: int,
                          *, iteration: int = 0, fingerprint: str, skipped: bool = False,
                          skip_reason: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO query_history
               (search_id, iteration, query, kind, fingerprint, result_count, skipped, skip_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, iteration, query, kind, fingerprint, result_count,
                int(skipped), skip_reason,
            ),
        )
        self.db.commit()

    def query_history(self, search_id: str) -> list[dict[str, Any]]:
        return [
            {
                "iteration": int(row["iteration"]),
                "query": row["query"],
                "kind": row["kind"],
                "fingerprint": row["fingerprint"],
                "result_count": int(row["result_count"]),
                "skipped": bool(row["skipped"]),
                "skip_reason": row["skip_reason"],
            }
            for row in self.db.execute(
                """SELECT iteration, query, kind, fingerprint, result_count, skipped, skip_reason
                   FROM query_history WHERE search_id=? ORDER BY id""",
                (search_id,),
            )
        ]

    def query_fingerprints(self, search_id: str) -> set[str]:
        return {
            str(row[0])
            for row in self.db.execute(
                "SELECT DISTINCT fingerprint FROM query_history WHERE search_id=? AND skipped=0",
                (search_id,),
            )
        }

    def get_session_state(self, search_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT session_state_json FROM searches WHERE id=?", (search_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown search_id: {search_id}")
        if not row["session_state_json"]:
            return default_session_state()
        payload = json.loads(row["session_state_json"])
        state = default_session_state()
        state.update(payload if isinstance(payload, dict) else {})
        return state

    def save_session_state(self, search_id: str, state: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE searches SET session_state_json=?, updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), utc_now(), search_id),
        )
        self.db.commit()

    def update_search_request(self, search_id: str, request: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE searches SET request_json=?, updated_at=? WHERE id=?",
            (json.dumps(request, ensure_ascii=False), utc_now(), search_id),
        )
        self.db.commit()

    def save_iteration(self, search_id: str, iteration: int, stage: str,
                       hypothesis: dict[str, Any] | None, query_summary: dict[str, Any],
                       stop_reason: str | None, *, event: str = "iterate") -> None:
        self.db.execute(
            """INSERT INTO search_iterations
               (search_id, iteration, event, stage, hypothesis_json, query_summary_json,
                stop_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, iteration, event, stage,
                json.dumps(hypothesis, ensure_ascii=False) if hypothesis is not None else None,
                json.dumps(query_summary, ensure_ascii=False),
                stop_reason, utc_now(),
            ),
        )
        self.db.commit()

    def list_iterations(self, search_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": int(row["id"]),
                "iteration": int(row["iteration"]),
                "event": row["event"],
                "stage": row["stage"],
                "hypothesis": json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None,
                "query_summary": json.loads(row["query_summary_json"]) if row["query_summary_json"] else {},
                "stop_reason": row["stop_reason"],
                "created_at": row["created_at"],
            }
            for row in self.db.execute(
                """SELECT id, iteration, event, stage, hypothesis_json, query_summary_json,
                          stop_reason, created_at
                   FROM search_iterations WHERE search_id=? ORDER BY id""",
                (search_id,),
            )
        ]

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

    def retain_search_candidates(self, search_id: str, full_names: list[str]) -> None:
        names = [name.lower() for name in full_names]
        if not names:
            self.db.execute("DELETE FROM search_candidates WHERE search_id=?", (search_id,))
        else:
            placeholders = ",".join("?" for _ in names)
            self.db.execute(
                f"DELETE FROM search_candidates WHERE search_id=? AND full_name NOT IN ({placeholders})",
                (search_id, *names),
            )
        self.db.commit()

    def list_search_index(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, request_json, mode, created_at, updated_at, stale,
                      incomplete_phase, session_state_json
               FROM searches ORDER BY updated_at DESC"""
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            search_id = str(row["id"])
            ranked = self.db.execute(
                "SELECT 1 FROM rankings WHERE search_id=?", (search_id,)
            ).fetchone() is not None
            candidate_count = int(self.db.execute(
                "SELECT COUNT(*) FROM search_candidates WHERE search_id=?", (search_id,),
            ).fetchone()[0])
            result.append({
                "id": search_id,
                "request": json.loads(row["request_json"]),
                "mode": row["mode"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "stale": bool(row["stale"]),
                "incomplete_phase": row["incomplete_phase"],
                "session_state": json.loads(row["session_state_json"]) if row["session_state_json"] else {},
                "ranked": ranked,
                "candidate_count": candidate_count,
            })
        return result

    def candidate_count(self, search_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM search_candidates WHERE search_id=?", (search_id,),
        ).fetchone()
        return int(row[0])

    def load_search(self, search_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown search_id: {search_id}")
        candidates = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload_json FROM search_candidates WHERE search_id=? ORDER BY full_name", (search_id,)
        )]
        return {**dict(row), "request": json.loads(row["request_json"]), "candidates": candidates}

    def query_count(self, search_id: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM queries WHERE search_id=?", (search_id,)).fetchone()
        return int(row[0])

    def normal_query_count(self, search_id: str) -> int:
        row = self.db.execute(
            """SELECT COUNT(*) FROM query_history
               WHERE search_id=? AND skipped=0
                 AND kind NOT LIKE 'confirmation_%'
                 AND kind NOT LIKE 'semantic_%'""",
            (search_id,),
        ).fetchone()
        return int(row[0])

    def semantic_query_count(self, search_id: str) -> int:
        row = self.db.execute(
            """SELECT COUNT(*) FROM query_history
               WHERE search_id=? AND skipped=0 AND kind LIKE 'semantic_%'""",
            (search_id,),
        ).fetchone()
        return int(row[0])

    def confirmation_query_count(self, search_id: str) -> int:
        row = self.db.execute(
            """SELECT COUNT(*) FROM query_history
               WHERE search_id=? AND skipped=0 AND kind LIKE 'confirmation_%'""",
            (search_id,),
        ).fetchone()
        return int(row[0])

    def get_candidate(self, full_name: str, search_id: str | None = None) -> dict[str, Any] | None:
        if search_id:
            row = self.db.execute(
                "SELECT payload_json FROM search_candidates WHERE search_id=? AND full_name=?",
                (search_id, full_name.lower()),
            ).fetchone()
        else:
            row = self.db.execute("SELECT snapshot_json FROM repositories WHERE full_name=?", (full_name.lower(),)).fetchone()
        return json.loads(row[0]) if row else None

    def save_ranking(self, search_id: str, ranking: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO rankings VALUES (?, ?, ?)",
            (search_id, json.dumps(ranking, ensure_ascii=False), utc_now()),
        )
        self.db.commit()

    def get_ranking(self, search_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT ranking_json FROM rankings WHERE search_id=?", (search_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_boundary_snapshot(self, search_id: str, stage: str,
                               boundary: dict[str, Any], *,
                               iteration: int | None = None,
                               hypothesis: dict[str, Any] | None = None,
                               query_summary: dict[str, Any] | None = None,
                               visible_repos: dict[str, Any] | list[str] | None = None) -> dict[str, Any]:
        previous = self.latest_boundary_snapshot(search_id)
        delta = boundary_delta(boundary, previous["boundary"] if previous else None).to_dict()
        self.db.execute(
            """INSERT INTO boundary_snapshots
               (search_id, stage, boundary_json, delta_json, created_at,
                iteration, hypothesis_json, query_summary_json, repos_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id, stage, json.dumps(boundary, ensure_ascii=False),
                json.dumps(delta, ensure_ascii=False), utc_now(),
                iteration,
                json.dumps(hypothesis, ensure_ascii=False) if hypothesis is not None else None,
                json.dumps(query_summary, ensure_ascii=False) if query_summary is not None else None,
                json.dumps(visible_repos, ensure_ascii=False) if visible_repos is not None else None,
            ),
        )
        self.db.commit()
        return delta

    def boundary_snapshots(self, search_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": int(row["id"]), "stage": row["stage"],
                "iteration": row["iteration"],
                "boundary": json.loads(row["boundary_json"]),
                "boundary_delta": json.loads(row["delta_json"]),
                "hypothesis": json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None,
                "query_summary": json.loads(row["query_summary_json"]) if row["query_summary_json"] else None,
                "visible_repos": json.loads(row["repos_json"]) if row["repos_json"] else None,
                "created_at": row["created_at"],
            }
            for row in self.db.execute(
                """SELECT id,stage,iteration,boundary_json,delta_json,hypothesis_json,
                          query_summary_json,repos_json,created_at
                   FROM boundary_snapshots WHERE search_id=? ORDER BY id""",
                (search_id,),
            )
        ]

    def latest_boundary_snapshot(self, search_id: str,
                                 stages: tuple[str, ...] = ()) -> dict[str, Any] | None:
        select = """SELECT id,stage,iteration,boundary_json,delta_json,hypothesis_json,
                           query_summary_json,repos_json,created_at
                    FROM boundary_snapshots"""
        if stages:
            placeholders = ",".join("?" for _ in stages)
            row = self.db.execute(
                f"""{select}
                    WHERE search_id=? AND stage IN ({placeholders})
                    ORDER BY id DESC LIMIT 1""",
                (search_id, *stages),
            ).fetchone()
        else:
            row = self.db.execute(
                f"{select} WHERE search_id=? ORDER BY id DESC LIMIT 1",
                (search_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]), "stage": row["stage"],
            "iteration": row["iteration"],
            "boundary": json.loads(row["boundary_json"]),
            "boundary_delta": json.loads(row["delta_json"]),
            "hypothesis": json.loads(row["hypothesis_json"]) if row["hypothesis_json"] else None,
            "query_summary": json.loads(row["query_summary_json"]) if row["query_summary_json"] else None,
            "visible_repos": json.loads(row["repos_json"]) if row["repos_json"] else None,
            "created_at": row["created_at"],
        }

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
        """Return rejection/difficulty adjustment for this exact repository.

        Positive and topic-neighbour feedback intentionally do not propagate:
        doing so would turn boundary discovery back into a similarity-based
        personalization loop. The topics argument remains for compatibility.
        """
        rows = self.db.execute(
            "SELECT relevant, interesting, too_hard FROM feedback WHERE full_name=?", (full_name.lower(),)
        ).fetchall()
        exact_values: list[float] = []
        for row in rows:
            rejection = (-0.6 if row[0] is not None and not bool(row[0]) else 0.0)
            rejection += (-0.3 if row[1] is not None and not bool(row[1]) else 0.0)
            difficulty = -0.1 if bool(row[2]) else 0.0
            exact_values.append(rejection + difficulty)
        value = sum(exact_values) / len(exact_values) if exact_values else 0.0
        return max(-1.0, min(0.0, value))

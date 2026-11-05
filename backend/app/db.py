"""SQLite 持久化。

刻意不用 ORM：表结构少、查询明确，手写 SQL 更易审查，也不引入迁移工具。
并发策略：单连接 + 线程锁 + WAL。写入都是毫秒级，不做连接池。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id      TEXT PRIMARY KEY,
    first_seen_ns  INTEGER NOT NULL,
    last_seen_ns   INTEGER NOT NULL,
    frames         INTEGER NOT NULL DEFAULT 0,
    last_status    TEXT,
    meta_json      TEXT
);

CREATE TABLE IF NOT EXISTS frames (
    frame_id           TEXT PRIMARY KEY,
    device_id          TEXT NOT NULL,
    session_id         TEXT,
    seq                INTEGER,
    ts_capture_ns      INTEGER NOT NULL,
    ts_received_ns     INTEGER NOT NULL,
    width              INTEGER,
    height             INTEGER,
    jpeg_bytes         INTEGER,
    depth_bytes        INTEGER,
    confidence_bytes   INTEGER,
    dropped_since_last INTEGER DEFAULT 0,
    status             TEXT NOT NULL,
    error              TEXT
);
CREATE INDEX IF NOT EXISTS idx_frames_received ON frames(ts_received_ns);
CREATE INDEX IF NOT EXISTS idx_frames_status ON frames(status);

CREATE TABLE IF NOT EXISTS assessments (
    frame_id           TEXT PRIMARY KEY REFERENCES frames(frame_id),
    device_id          TEXT NOT NULL,
    ts_assessed_ns     INTEGER NOT NULL,
    overall_risk       TEXT NOT NULL,
    system_status      TEXT NOT NULL,
    a_to_b             TEXT,
    b_to_a             TEXT,
    alternative_status TEXT,
    road_detected      INTEGER,
    road_status        TEXT,
    road_coverage      REAL,
    rock_count         INTEGER,
    processing_ms      REAL,
    json               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assess_ts ON assessments(ts_assessed_ns);
CREATE INDEX IF NOT EXISTS idx_assess_risk ON assessments(overall_risk);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns     INTEGER NOT NULL,
    frame_id  TEXT,
    device_id TEXT,
    level     TEXT NOT NULL,
    code      TEXT NOT NULL,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ns);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """给老库补列。SQLite 只支持 ADD COLUMN，够用。"""
        additions = {
            "frames": [("artifact_dir", "TEXT"), ("rgb_path", "TEXT"),
                       ("depth_path", "TEXT"), ("confidence_path", "TEXT"),
                       ("processed_ns", "INTEGER")],
            "devices": [("last_heartbeat_ns", "INTEGER"), ("thermal", "TEXT"),
                        ("target_fps", "INTEGER")],
        }
        for table, columns in additions.items():
            existing = {row[1] for row in
                        self._conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns:
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    # ---------- 写入 ----------

    def upsert_device(self, device_id: str, ts_ns: int, meta: dict | None = None,
                      count_frame: bool = True) -> None:
        thermal = (meta or {}).get("thermal")
        fps = (meta or {}).get("target_fps")
        with self._lock:
            self._conn.execute(
                """INSERT INTO devices (device_id, first_seen_ns, last_seen_ns, frames,
                                        meta_json, last_heartbeat_ns, thermal, target_fps)
                   VALUES (?, ?, ?, 0, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET
                     last_seen_ns = excluded.last_seen_ns,
                     frames = devices.frames + ?,
                     meta_json = COALESCE(excluded.meta_json, devices.meta_json),
                     last_heartbeat_ns = COALESCE(excluded.last_heartbeat_ns,
                                                  devices.last_heartbeat_ns),
                     thermal = COALESCE(excluded.thermal, devices.thermal),
                     target_fps = COALESCE(excluded.target_fps, devices.target_fps)""",
                (device_id, ts_ns, ts_ns,
                 json.dumps(meta, ensure_ascii=False) if meta else None,
                 ts_ns, thermal, fps,
                 1 if count_frame else 0))
            self._conn.commit()

    def insert_frame(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO frames
                   (frame_id, device_id, session_id, seq, ts_capture_ns, ts_received_ns,
                    width, height, jpeg_bytes, depth_bytes, confidence_bytes,
                    dropped_since_last, status, error,
                    artifact_dir, rgb_path, depth_path, confidence_path)
                   VALUES (:frame_id, :device_id, :session_id, :seq, :ts_capture_ns,
                           :ts_received_ns, :width, :height, :jpeg_bytes, :depth_bytes,
                           :confidence_bytes, :dropped_since_last, :status, :error,
                           :artifact_dir, :rgb_path, :depth_path, :confidence_path)""",
                frame)
            self._conn.commit()

    def mark_frame(self, frame_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE frames SET status = ?, error = ?, processed_ns = ? WHERE frame_id = ?",
                (status, error, now_ns(), frame_id))
            self._conn.commit()

    def insert_assessment(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO assessments
                   (frame_id, device_id, ts_assessed_ns, overall_risk, system_status,
                    a_to_b, b_to_a, alternative_status, road_detected, road_status,
                    road_coverage, rock_count, processing_ms, json)
                   VALUES (:frame_id, :device_id, :ts_assessed_ns, :overall_risk,
                           :system_status, :a_to_b, :b_to_a, :alternative_status,
                           :road_detected, :road_status, :road_coverage, :rock_count,
                           :processing_ms, :json)""", row)
            self._conn.commit()

    def insert_event(self, ts_ns: int, frame_id: str | None, device_id: str | None,
                     level: str, code: str, message: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts_ns, frame_id, device_id, level, code, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts_ns, frame_id, device_id, level, code, message))
            self._conn.commit()

    # ---------- 查询 ----------

    def latest_assessment(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM assessments ORDER BY ts_assessed_ns DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def frame(self, frame_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM frames WHERE frame_id = ?",
                                     (frame_id,)).fetchone()
        return dict(row) if row else None

    def frames_pending(self, limit: int = 4) -> list[dict]:
        """等待 AI 处理的帧（按到达顺序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM frames WHERE status = 'stored' "
                "ORDER BY ts_received_ns ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def expire_stale_frames(self, older_than_ns: int) -> list[str]:
        """把长时间没等到 AI 结果的帧标记为 expired，返回 frame_id 列表。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT frame_id FROM frames WHERE status = 'stored' AND ts_received_ns < ?",
                (older_than_ns,)).fetchall()
            ids = [r["frame_id"] for r in rows]
            for frame_id in ids:
                self._conn.execute(
                    "UPDATE frames SET status = 'expired', error = 'no AI result in time' "
                    "WHERE frame_id = ?", (frame_id,))
            self._conn.commit()
        return ids

    def device_online(self, offline_after_ns: int) -> list[dict]:
        now = now_ns()
        devices = self.list_devices()
        for device in devices:
            last = device.get("last_heartbeat_ns") or device["last_seen_ns"]
            device["online"] = (now - last) < offline_after_ns
            device["silent_ms"] = int((now - last) / 1_000_000)
        return devices

    def all_frame_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT frame_id FROM frames").fetchall()
        return [r["frame_id"] for r in rows]

    def counters(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM frames GROUP BY status").fetchall()
        counts = {row[0]: row[1] for row in rows}
        return {
            "queued": counts.get("stored", 0),
            "processed": counts.get("processed", 0),
            "failed": counts.get("failed", 0) + counts.get("expired", 0),
            "dropped": counts.get("dropped", 0),
        }

    def list_assessments(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM assessments ORDER BY ts_assessed_ns DESC LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY ts_ns DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def list_devices(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY last_seen_ns DESC").fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            frames = self._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
            done = self._conn.execute(
                "SELECT COUNT(*) FROM frames WHERE status = 'done'").fetchone()[0]
            failed = self._conn.execute(
                "SELECT COUNT(*) FROM frames WHERE status = 'failed'").fetchone()[0]
            dropped = self._conn.execute(
                "SELECT COUNT(*) FROM frames WHERE status = 'dropped'").fetchone()[0]
            assessments = self._conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
            events = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {"frames": frames, "processed": done, "failed": failed,
                "dropped": dropped, "assessments": assessments, "events": events}

    # ---------- 清理 ----------

    def purge_older_than(self, cutoff_ns: int) -> dict:
        """删除早于 cutoff 的帧、判定与事件。返回删除条数。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM assessments WHERE ts_assessed_ns < ?", (cutoff_ns,))
            deleted_assess = cur.rowcount
            cur = self._conn.execute("DELETE FROM frames WHERE ts_received_ns < ?", (cutoff_ns,))
            deleted_frames = cur.rowcount
            cur = self._conn.execute("DELETE FROM events WHERE ts_ns < ?", (cutoff_ns,))
            deleted_events = cur.rowcount
            self._conn.commit()
        return {"frames": deleted_frames, "assessments": deleted_assess,
                "events": deleted_events}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def now_ns() -> int:
    return time.time_ns()

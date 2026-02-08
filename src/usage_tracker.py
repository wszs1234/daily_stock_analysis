# -*- coding: utf-8 -*-
"""
用量监控模块，记录每个用户的 API 调用与分析次数。
"""
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional


def _get_db_path() -> str:
    base = os.getenv("USAGE_DB_PATH", "./data/usage.db")
    path = Path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _init_db() -> None:
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                action_type TEXT NOT NULL,
                stock_code TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_logs(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_logs(created_at)")
        conn.commit()
    finally:
        conn.close()


def record_usage(user_id: int, username: str, action_type: str, stock_code: Optional[str] = None) -> None:
    """记录一次用量"""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute(
            "INSERT INTO usage_logs (user_id, username, action_type, stock_code, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, action_type, stock_code or "", datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_stats(user_id: Optional[int] = None, username: Optional[str] = None) -> list:
    """
    获取用量统计。
    若 user_id 和 username 均为 None，则返回所有用户的汇总。
    """
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        if user_id is not None:
            rows = conn.execute(
                """
                SELECT username, action_type, COUNT(*) as cnt
                FROM usage_logs WHERE user_id = ?
                GROUP BY username, action_type
                ORDER BY cnt DESC
                """,
                (user_id,),
            ).fetchall()
        elif username:
            rows = conn.execute(
                """
                SELECT username, action_type, COUNT(*) as cnt
                FROM usage_logs WHERE username = ?
                GROUP BY username, action_type
                ORDER BY cnt DESC
                """,
                (username,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT username, action_type, COUNT(*) as cnt
                FROM usage_logs
                GROUP BY username, action_type
                ORDER BY username, cnt DESC
                """
            ).fetchall()
        return [{"username": r[0], "action_type": r[1], "count": r[2]} for r in rows]
    finally:
        conn.close()


def get_recent_logs(limit: int = 100) -> list:
    """获取最近的用量记录"""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        rows = conn.execute(
            "SELECT user_id, username, action_type, stock_code, created_at FROM usage_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"user_id": r[0], "username": r[1], "action_type": r[2], "stock_code": r[3], "created_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()

# -*- coding: utf-8 -*-
"""
用户注册与登录模块，用于 run_new.py 的访问控制。
"""
import os
import sqlite3
import hashlib
import secrets
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple


def _get_db_path() -> str:
    """获取用户数据库路径"""
    base = os.getenv("USER_DB_PATH", "./data/users.db")
    path = Path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _hash_password(password: str) -> str:
    """密码加盐哈希"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """验证密码"""
    try:
        salt, h = stored.split("$", 1)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return secrets.compare_digest(computed.hex(), h)
    except Exception:
        return False


def _init_db() -> None:
    """初始化用户表"""
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def register(username: str, password: str, email: str, phone: str) -> Tuple[bool, str]:
    """
    注册新用户。
    返回 (成功, 消息)。
    """
    username = (username or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()

    if not username:
        return False, "用户名不能为空"
    if len(username) < 2:
        return False, "用户名至少 2 个字符"
    if not password or len(password) < 6:
        return False, "密码至少 6 位"
    if not email:
        return False, "邮箱不能为空"
    if not phone:
        return False, "手机号不能为空"

    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, email, phone, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, _hash_password(password), email, phone, datetime.now().isoformat()),
        )
        conn.commit()
        return True, "注册成功"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    except Exception as e:
        return False, f"注册失败: {e}"
    finally:
        conn.close()


def login(username: str, password: str) -> Tuple[bool, Optional[dict], str]:
    """
    登录验证。
    返回 (成功, 用户信息字典或 None, 消息)。
    """
    username = (username or "").strip()
    if not username or not password:
        return False, None, "用户名和密码不能为空"

    _init_db()
    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, email, phone, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return False, None, "用户名或密码错误"
        uid, uname, pwhash, email, phone, created = row
        if not _verify_password(password, pwhash):
            return False, None, "用户名或密码错误"
        return True, {"id": uid, "username": uname, "email": email, "phone": phone, "created_at": created}, "登录成功"
    finally:
        conn.close()

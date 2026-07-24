"""
数据库模块 —— SQLite 用户/收藏/历史 数据持久化。
"""
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表（幂等）。"""
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            avatar_path TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            config_json TEXT NOT NULL,
            budget REAL NOT NULL,
            scenario TEXT NOT NULL,
            total_price REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS generation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            config_json TEXT NOT NULL,
            budget REAL NOT NULL,
            scenario TEXT NOT NULL,
            total_price REAL NOT NULL,
            performance_json TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()


# ==================== 用户 ====================

def create_user(account: str, password_hash: str) -> dict | None:
    try:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO users (account, password_hash, nickname) VALUES (?, ?, ?)",
            (account, password_hash, account)
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()
        return get_user_by_id(uid)
    except sqlite3.IntegrityError:
        return None


def get_user_by_account(account: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE account = ?", (account,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(uid: int) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_nickname(uid: int, nickname: str) -> bool:
    conn = _connect()
    conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, uid))
    conn.commit()
    conn.close()
    return True


def update_user_avatar(uid: int, avatar_path: str) -> bool:
    conn = _connect()
    conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path, uid))
    conn.commit()
    conn.close()
    return True


# ==================== 收藏 ====================

def get_favorites(user_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_favorite(user_id: int, budget: float, scenario: str, total_price: float) -> dict | None:
    conn = _connect()
    row = conn.execute(
        """SELECT * FROM favorites
           WHERE user_id = ? AND budget = ? AND scenario = ? AND total_price = ?
           LIMIT 1""",
        (user_id, budget, scenario, total_price)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_favorite(user_id: int, config_json: str, budget: float, scenario: str, total_price: float) -> int:
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO favorites (user_id, config_json, budget, scenario, total_price) VALUES (?, ?, ?, ?, ?)",
        (user_id, config_json, budget, scenario, total_price)
    )
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid


def remove_favorite(fav_id: int, user_id: int) -> bool:
    conn = _connect()
    conn.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (fav_id, user_id))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


# ==================== 生成历史 ====================

def get_history(user_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM generation_history WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_history(user_id: int, config_json: str, budget: float, scenario: str,
                total_price: float, performance_json: str = "") -> int:
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO generation_history
           (user_id, config_json, budget, scenario, total_price, performance_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, config_json, budget, scenario, total_price, performance_json)
    )
    hid = cur.lastrowid
    conn.commit()
    conn.close()
    return hid


def remove_history(hid: int, user_id: int) -> bool:
    conn = _connect()
    conn.execute("DELETE FROM generation_history WHERE id = ? AND user_id = ?", (hid, user_id))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0

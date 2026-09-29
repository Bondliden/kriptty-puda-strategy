"""Persistencia mínima en SQLite: estado de estrategias + diario de órdenes.

Sustituye a Postgres/Redis del diseño original, que el código nunca llegó a
usar; las estrategias con memoria (grid, DCA, pares de funding) perdían su
estado en cada reinicio.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: str = "data/state.db"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (ns TEXT, key TEXT, value TEXT, PRIMARY KEY (ns, key))"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS journal (ts REAL, account TEXT, tag TEXT, symbol TEXT,"
                " side TEXT, amount REAL, price REAL, stop_loss REAL, take_profit REAL,"
                " mode TEXT, status TEXT, detail TEXT)"
            )

    def get(self, ns: str, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE ns=? AND key=?", (ns, key)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, ns: str, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (ns, key, value) VALUES (?, ?, ?)"
                " ON CONFLICT(ns, key) DO UPDATE SET value=excluded.value",
                (ns, key, json.dumps(value, default=str)),
            )

    def delete(self, ns: str, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM kv WHERE ns=? AND key=?", (ns, key))

    def all(self, ns: str) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM kv WHERE ns=?", (ns,)).fetchall()
        return {k: json.loads(v) for k, v in rows}

    def journal(self, **row: Any) -> None:
        cols = ["account", "tag", "symbol", "side", "amount", "price", "stop_loss", "take_profit",
                "mode", "status", "detail"]
        values = [time.time()] + [row.get(c) for c in cols]
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO journal (ts, {', '.join(cols)}) VALUES ({', '.join('?' * len(values))})",
                values,
            )

    def recent_journal(self, limit: int = 50, account: str | None = None) -> list[dict]:
        sql = "SELECT * FROM journal"
        args: tuple = ()
        if account:
            sql += " WHERE account=?"
            args = (account,)
        sql += " ORDER BY ts DESC LIMIT ?"
        with self._lock:
            cur = self._conn.execute(sql, args + (limit,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

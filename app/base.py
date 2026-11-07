"""База лидов: организации, которые пользователь отложил для работы.

Хранится в SQLite (leads.db в папке настроек программы) и переживает
перезапуски и новые сборы. У каждого лида есть статус и заметка.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .records import FIELDS

STATUSES = {
    'new': 'Новый',
    'contacted': 'Написал',
    'replied': 'Ответил',
    'deal': 'Сделка',
    'refused': 'Отказ',
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id         TEXT PRIMARY KEY,
  data       TEXT NOT NULL,
  name       TEXT,
  status     TEXT NOT NULL DEFAULT 'new',
  note       TEXT NOT NULL DEFAULT '',
  added_at   REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""


def lead_id(rec: Dict[str, Any]) -> str:
    """ID карточки 2ГИС, а если его нет — отпечаток «название + адрес»."""
    if rec.get('_id'):
        return str(rec['_id'])
    key = '%s|%s' % ((rec.get('name') or '').strip().lower(), (rec.get('address') or '').strip().lower())
    return 'h' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]


class LeadBase:
    def __init__(self, path: Path) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Добавить (или обновить контакты, не трогая статус и заметку)."""
        lid = lead_id(rec)
        data = {k: rec.get(k) for k in list(FIELDS) + ['_query']}
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO leads(id, data, name, added_at, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data, name=excluded.name""",
                (lid, json.dumps(data, ensure_ascii=False), data.get('name'), now, now))
        return self.get(lid) or {}

    def remove(self, lid: str) -> None:
        with self._lock:
            self._db.execute('DELETE FROM leads WHERE id=?', (lid,))

    def set_status(self, lid: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError('Неизвестный статус')
        with self._lock:
            self._db.execute('UPDATE leads SET status=?, updated_at=? WHERE id=?', (status, time.time(), lid))

    def set_note(self, lid: str, note: str) -> None:
        with self._lock:
            self._db.execute('UPDATE leads SET note=?, updated_at=? WHERE id=?',
                             ((note or '')[:2000], time.time(), lid))

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = json.loads(r['data'])
        d.update({'id': r['id'], 'status': r['status'], 'status_title': STATUSES.get(r['status'], r['status']),
                  'note': r['note'], 'added_at': r['added_at'], 'updated_at': r['updated_at'],
                  'added': time.strftime('%d.%m.%Y', time.localtime(r['added_at']))})
        return d

    def get(self, lid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
        return self._row(r) if r else None

    def ids(self) -> List[str]:
        with self._lock:
            return [r[0] for r in self._db.execute('SELECT id FROM leads')]

    def list(self, status: str = '', search: str = '') -> List[Dict[str, Any]]:
        where, args = [], []
        if status:
            where.append('status=?')
            args.append(status)
        sql = 'SELECT * FROM leads' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY updated_at DESC'
        with self._lock:
            rows = [self._row(r) for r in self._db.execute(sql, args)]
        q = (search or '').strip().lower().replace('ё', 'е')
        if q:  # поиск по всем полям карточки и заметке (lower() в SQLite не знает кириллицу)
            rows = [r for r in rows
                    if q in ' '.join(str(v) for v in r.values() if v).lower().replace('ё', 'е')]
        return rows

    def counts(self) -> Dict[str, int]:
        with self._lock:
            c = {r[0]: r[1] for r in self._db.execute('SELECT status, COUNT(*) FROM leads GROUP BY status')}
        c['all'] = sum(c.values())
        return c

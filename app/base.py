"""База лидов: организации, которые пользователь отложил для работы.

Хранится в SQLite (leads.db в папке настроек программы) и переживает
перезапуски и новые сборы. У каждого лида есть статус и заметка.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .records import FIELDS

STATUSES = {
    'new': 'Новый',
    'demo': 'Демо готово',
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
CREATE TABLE IF NOT EXISTS templates (
  id    TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body  TEXT NOT NULL,
  pos   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sends (lead_id TEXT, channel TEXT, ok INTEGER, info TEXT, at REAL);
CREATE TABLE IF NOT EXISTS batches (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT '',
  meta       TEXT NOT NULL DEFAULT '{}',
  state      TEXT NOT NULL DEFAULT 'running',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_items (
  batch_id  TEXT NOT NULL,
  lead_id   TEXT NOT NULL,
  pos       INTEGER NOT NULL,
  site_kind TEXT NOT NULL DEFAULT '',
  data      TEXT NOT NULL,
  PRIMARY KEY (batch_id, lead_id)
);
"""

# Колонки, добавленные после первой версии базы: старые leads.db дополняются при запуске.
_MIGRATIONS = [
    ('demo_url', "ALTER TABLE leads ADD COLUMN demo_url TEXT NOT NULL DEFAULT ''"),
    ('demo', "ALTER TABLE leads ADD COLUMN demo TEXT NOT NULL DEFAULT ''"),
    ('review_audit', "ALTER TABLE leads ADD COLUMN review_audit TEXT NOT NULL DEFAULT ''"),
    ('review_collection', "ALTER TABLE leads ADD COLUMN review_collection TEXT NOT NULL DEFAULT ''"),
]

DEFAULT_TEMPLATES = [
    ('nosite', 'Нет сайта',
     'Здравствуйте! Нашёл «{название}» в 2ГИС — у вас рейтинг {рейтинг}, а своего сайта нет. '
     'Клиенты из поиска часто уходят к тем, у кого он есть.\n\n'
     'Набросал, как мог бы выглядеть ваш сайт: {демо}\n'
     'Если нравится — доделаю под вас за пару дней. Интересно?'),
    ('social', 'Только соцсети',
     'Здравствуйте! Вижу, что «{название}» принимает клиентов через соцсети. '
     'Сайт с услугами и кнопкой записи добавляет заявок из Яндекса и 2ГИС.\n\n'
     'Сделал пример для вас: {демо}\nПодскажите, кому можно показать?'),
    ('short', 'Коротко',
     'Здравствуйте! Сделал демо-сайт для «{название}»: {демо}\nЕсли интересно — расскажу, как запустить.'),
]

TEMPLATE_VARS = ['{название}', '{категория}', '{город}', '{адрес}', '{рейтинг}', '{отзывов}', '{демо}']


def render_message(body: str, lead: Dict[str, Any]) -> str:
    """Подставляет данные лида в шаблон сообщения."""
    rating = lead.get('rating')
    vals = {
        '{название}': lead.get('name') or '',
        '{категория}': (lead.get('category') or '').split(',')[0].strip().lower(),
        '{город}': lead.get('city') or '',
        '{адрес}': lead.get('address') or '',
        '{рейтинг}': ('%.1f' % rating).replace('.', ',') if isinstance(rating, (int, float)) and rating else '',
        '{отзывов}': str(lead.get('reviews') or ''),
        '{демо}': lead.get('demo_url') or '',
    }
    out = body or ''
    name = vals['{название}']
    if re.search(r'[«"“]', name):  # «Кофейня «Тест»» → Кофейня «Тест»
        out = re.sub(r'[«"“]\{название\}[»"”]', lambda m: name, out)
    for k, v in vals.items():
        out = out.replace(k, v)
    out = re.sub(r'[ \t]+([,.!?:])', r'\1', out)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    out = re.sub(r':\s*\n', ':\n', out)
    return re.sub(r'\n{3,}', '\n\n', out).strip()



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
            cols = {r[1] for r in self._db.execute('PRAGMA table_info(leads)')}
            for col, sql in _MIGRATIONS:
                if col not in cols:
                    self._db.execute(sql)
            if not self._db.execute("SELECT v FROM meta WHERE k='templates_seeded'").fetchone():
                for i, (tid, title, body) in enumerate(DEFAULT_TEMPLATES):
                    self._db.execute('INSERT OR IGNORE INTO templates(id,title,body,pos) VALUES (?,?,?,?)',
                                     (tid, title, body, i))
                self._db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('templates_seeded','1')")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Добавить (или обновить контакты, не трогая статус и заметку)."""
        lid = lead_id(rec)
        data = {k: rec.get(k) for k in list(FIELDS) + ['_query', '_photos']}
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO leads(id, data, name, added_at, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data, name=excluded.name""",
                (lid, json.dumps(data, ensure_ascii=False), data.get('name'), now, now))
        return self.get(lid) or {}

    def add_many(self, recs: List[Dict[str, Any]]) -> int:
        """Пачкой в работу; уже взятые не трогаем. Возвращает, сколько добавлено новых."""
        now = time.time()
        added = 0
        with self._lock:
            self._db.execute('BEGIN')
            try:
                for i, rec in enumerate(recs):
                    if not rec or not rec.get('name'):
                        continue
                    data = {k: rec.get(k) for k in list(FIELDS) + ['_query', '_photos']}
                    cur = self._db.execute(
                        'INSERT OR IGNORE INTO leads(id, data, name, added_at, updated_at) VALUES (?,?,?,?,?)',
                        (rec.get('_lid') or lead_id(rec), json.dumps(data, ensure_ascii=False), data.get('name'), now, now - i * 1e-3))
                    added += cur.rowcount
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise
        return added

    def marks(self) -> Dict[str, str]:
        """id -> статус для всех, кто в работе: по ним новые сборы помечают дубли."""
        with self._lock:
            return {r[0]: r[1] for r in self._db.execute('SELECT id, status FROM leads')}

    # ----- сборы: всё, что напарсили, сохраняется само -----
    def batch_create(self, title: str, source: str, meta: Dict[str, Any]) -> str:
        bid = 'b%d' % int(time.time() * 1000)
        now = time.time()
        with self._lock:
            self._db.execute('INSERT INTO batches(id,title,source,meta,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                             (bid, title[:200], source, json.dumps(meta, ensure_ascii=False), 'running', now, now))
        return bid

    def batch_sync(self, bid: str, rows: List[Dict[str, Any]], state: Optional[str] = None) -> None:
        """Записывает текущие строки сбора (строки могут дополняться — перезаписываем целиком)."""
        items, seen = [], set()
        for r in rows:
            lid = lead_id(r)
            if lid in seen:
                continue
            seen.add(lid)
            data = {k: r.get(k) for k in list(FIELDS) + ['_query', '_photos']}
            items.append((bid, lid, len(items), data.get('site_kind') or '', json.dumps(data, ensure_ascii=False)))
        with self._lock:
            self._db.execute('BEGIN')
            try:
                self._db.execute('DELETE FROM batch_items WHERE batch_id=?', (bid,))
                self._db.executemany('INSERT INTO batch_items(batch_id,lead_id,pos,site_kind,data) VALUES (?,?,?,?,?)', items)
                self._db.execute('UPDATE batches SET updated_at=?, state=COALESCE(?, state) WHERE id=?',
                                 (time.time(), state, bid))
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise

    def batch_finish(self, bid: str, state: str) -> None:
        with self._lock:
            n = self._db.execute('SELECT COUNT(*) FROM batch_items WHERE batch_id=?', (bid,)).fetchone()[0]
            if not n:  # пустой сбор не храним
                self._db.execute('DELETE FROM batches WHERE id=?', (bid,))
            else:
                self._db.execute('UPDATE batches SET state=?, updated_at=? WHERE id=?', (state, time.time(), bid))

    def batches(self) -> List[Dict[str, Any]]:
        sql = """SELECT b.*, COUNT(i.lead_id) AS total,
                        SUM(i.site_kind='Без сайта') AS nosite,
                        SUM(l.id IS NOT NULL) AS taken,
                        SUM(l.status IS NOT NULL AND l.status NOT IN ('new','demo')) AS contacted
                 FROM batches b LEFT JOIN batch_items i ON i.batch_id=b.id
                 LEFT JOIN leads l ON l.id=i.lead_id
                 GROUP BY b.id ORDER BY b.created_at DESC"""
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r['meta'])
            except ValueError:
                meta = {}
            out.append({'id': r['id'], 'title': r['title'], 'source': r['source'], 'meta': meta, 'state': r['state'],
                        'when': time.strftime('%d.%m.%Y %H:%M', time.localtime(r['created_at'])),
                        'total': r['total'] or 0, 'nosite': r['nosite'] or 0,
                        'taken': r['taken'] or 0, 'contacted': r['contacted'] or 0})
        return out

    def batch_items(self, bid: str, only_nosite: bool = False) -> List[Dict[str, Any]]:
        sql = ('SELECT i.lead_id, i.data FROM batch_items i WHERE i.batch_id=?'
               + (" AND i.site_kind='Без сайта'" if only_nosite else '') + ' ORDER BY i.pos')
        with self._lock:
            rows = self._db.execute(sql, (bid,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r['data'])
            d['_lid'] = r['lead_id']
            out.append(d)
        return out

    def batch_title(self, bid: str) -> str:
        with self._lock:
            r = self._db.execute('SELECT title FROM batches WHERE id=?', (bid,)).fetchone()
        return r[0] if r else ''

    def batch_delete(self, bid: str) -> None:
        with self._lock:
            self._db.execute('DELETE FROM batch_items WHERE batch_id=?', (bid,))
            self._db.execute('DELETE FROM batches WHERE id=?', (bid,))

    def close_running_batches(self) -> None:
        """После вылета программы: незавершённые сборы помечаем остановленными."""
        with self._lock:
            self._db.execute("UPDATE batches SET state='stopped' WHERE state='running'")

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
                  'demo_url': r['demo_url'], 'has_demo': bool(r['demo']),
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

    def demo_urls(self) -> Dict[str, str]:
        with self._lock:
            return {r[0]: r[1] for r in self._db.execute("SELECT id, demo_url FROM leads WHERE demo_url<>''")}

    def get_demo(self, lid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._db.execute('SELECT demo FROM leads WHERE id=?', (lid,)).fetchone()
        if not r or not r[0]:
            return None
        try:
            return json.loads(r[0])
        except ValueError:
            return None

    def save_demo(self, lid: str, content: Dict[str, Any], url: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute('UPDATE leads SET demo=?, updated_at=? WHERE id=?',
                             (json.dumps(content, ensure_ascii=False), time.time(), lid))
            self._db.execute("UPDATE leads SET status='demo' WHERE id=? AND status='new'", (lid,))
            if url is not None:
                self._db.execute('UPDATE leads SET demo_url=? WHERE id=?', (url, lid))

    def get_audit(self, lid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                'SELECT review_audit, review_collection FROM leads WHERE id=?', (lid,)
            ).fetchone()
        if row is None:
            return {}
        return {'report': json.loads(row['review_audit'] or '{}'),
                'collection': json.loads(row['review_collection'] or '{}')}

    def save_review_collection(self, lid: str, sample: Dict[str, Any]) -> bool:
        with self._lock:
            return self._db.execute('UPDATE leads SET review_collection=? WHERE id=?',
                                    (json.dumps(sample, ensure_ascii=False), lid)).rowcount == 1

    def save_review_report(self, lid: str, sample: Dict[str, Any], report: Dict[str, Any]) -> bool:
        with self._lock:
            return self._db.execute(
                'UPDATE leads SET review_audit=? WHERE id=? AND review_collection=?',
                (json.dumps(report, ensure_ascii=False), lid, json.dumps(sample, ensure_ascii=False)),
            ).rowcount == 1

    # ----- шаблоны сообщений -----
    def templates(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute('SELECT id, title, body FROM templates ORDER BY pos, rowid')]

    def save_template(self, tid: Optional[str], title: str, body: str) -> Dict[str, Any]:
        title = (title or '').strip()[:80] or 'Без названия'
        body = (body or '').strip()[:4000]
        with self._lock:
            if tid and self._db.execute('SELECT 1 FROM templates WHERE id=?', (tid,)).fetchone():
                self._db.execute('UPDATE templates SET title=?, body=? WHERE id=?', (title, body, tid))
            else:
                tid = 't%d' % int(time.time() * 1000)
                pos = self._db.execute('SELECT COALESCE(MAX(pos), 0) + 1 FROM templates').fetchone()[0]
                self._db.execute('INSERT INTO templates(id,title,body,pos) VALUES (?,?,?,?)', (tid, title, body, pos))
        return {'id': tid, 'title': title, 'body': body}

    def delete_template(self, tid: str) -> None:
        with self._lock:
            self._db.execute('DELETE FROM templates WHERE id=?', (tid,))

    # ----- журнал отправок (автоотправка в Telegram) -----
    def log_send(self, lid: str, channel: str, ok: bool, info: str = '') -> None:
        with self._lock:
            self._db.execute('INSERT INTO sends(lead_id, channel, ok, info, at) VALUES (?,?,?,?,?)',
                             (lid, channel, 1 if ok else 0, info, time.time()))
            if ok:
                self._db.execute("UPDATE leads SET status='contacted', updated_at=? WHERE id=? AND status IN ('new','demo')",
                                 (time.time(), lid))
            elif info:
                self._db.execute("UPDATE leads SET note=CASE WHEN note='' THEN ? ELSE note || char(10) || ? END "
                                 "WHERE id=?", ('Telegram: ' + info, 'Telegram: ' + info, lid))

    def sent_today(self, channel: str = 'telegram') -> int:
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        with self._lock:
            return self._db.execute('SELECT COUNT(*) FROM sends WHERE channel=? AND ok=1 AND at>=?',
                                    (channel, midnight)).fetchone()[0]

    def was_sent(self, lid: str, channel: str = 'telegram') -> bool:
        with self._lock:
            return bool(self._db.execute('SELECT 1 FROM sends WHERE lead_id=? AND channel=? AND ok=1',
                                         (lid, channel)).fetchone())

    def counts(self) -> Dict[str, int]:
        with self._lock:
            c = {r[0]: r[1] for r in self._db.execute('SELECT status, COUNT(*) FROM leads GROUP BY status')}
        c['all'] = sum(c.values())
        return c

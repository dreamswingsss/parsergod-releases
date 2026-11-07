"""Лиды: локальная база (SQLite), тип лида, статусы и шаблоны сообщений.

Каждая найденная организация сохраняется сюда автоматически. Вкладка «Парсер»
показывает лиды, которые ещё не добавлены в базу. Вкладка «База» показывает
добавленные, со статусами работы. Файл лежит в папке настроек программы
(leads.db) и не пропадает между запусками.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------- тип лида ----------

# Конструкторы и «сайты-визитки»: у бизнеса формально есть ссылка, но сайта как такового нет.
TEMPLATE_HOSTS = (
    'taplink.', 'tilda.ws', 'tilda.cc', 'wixsite.com', 'wix.com', 'ukit.me', 'ukit.com',
    'nethouse.ru', 'flexbe.', 'mssg.me', 'hipolink.', 'linktr.ee', 'taplink.cc', 'taplink.ws',
    'clients.site', 'business.site', 'yandex.ru/sprav', 'turbo.site', 'ucoz.', 'narod.ru',
    'jimdo', 'webflow.io', 'readymag.', 'tobiz.', 'lpmotor.', 'mya5.ru', 'yclients.com',
    'dikidi.', 'n.dikidi', 'beautybox.', 'linkin.bio', 'msite.', 'site123.', 'ru.tb.ru',
    'wordpress.com', 'blogspot.', 'ok.me', 'vk.link', 'taplink.at',
)
# Ссылки на соцсети и мессенджеры, которые 2ГИС иногда кладёт в поле «Сайт».
SOCIAL_HOSTS = (
    'vk.com', 'vk.ru', 'm.vk.com', 'instagram.com', 'instagr.am', 't.me', 'telegram.me',
    'wa.me', 'whatsapp.com', 'ok.ru', 'facebook.com', 'fb.com', 'youtube.com', 'tiktok.com',
    'viber.', 'dzen.ru', 'zen.yandex', 'avito.ru', 'max.ru',
)
SOCIAL_FIELDS = ('instagram', 'vkontakte', 'telegram', 'whatsapp', 'viber', 'odnoklassniki', 'youtube')

KINDS = {
    'nosite': 'Без сайта',
    'social': 'Только соцсети',
    'template': 'Шаблон',
    'site': 'Есть сайт',
}

STATUSES = {
    'new': 'Новый',
    'contacted': 'Написал',
    'replied': 'Ответил',
    'deal': 'Сделка',
    'refused': 'Отказ',
}


def _host(url: str) -> str:
    u = url.strip().lower()
    if not re.match(r'^[a-z]+://', u):
        u = 'https://' + u
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    host = (p.hostname or '').removeprefix('www.')
    return host + (p.path or '')


def _matches(host: str, needles: Iterable[str]) -> bool:
    return any(n in host for n in needles)


def classify(rec: Dict[str, Any]) -> str:
    """nosite / social / template / site — по полю «Сайт» и соцсетям."""
    sites = [s.strip() for s in (rec.get('website') or '').split(',') if s.strip()]
    real, template, social = [], [], []
    for s in sites:
        h = _host(s)
        if _matches(h, SOCIAL_HOSTS):
            social.append(s)
        elif _matches(h, TEMPLATE_HOSTS):
            template.append(s)
        else:
            real.append(s)
    if real:
        return 'site'
    if template:
        return 'template'
    if social or any(rec.get(f) for f in SOCIAL_FIELDS):
        return 'social'
    return 'nosite'


def mobile_digits(phone: Optional[str]) -> Optional[str]:
    """Первый мобильный номер РФ/КЗ (+7 9xx / +7 7xx) цифрами: '79001234567'."""
    for p in (phone or '').split(','):
        d = re.sub(r'\D', '', p)
        if len(d) == 11 and d[0] in '78':
            d = '7' + d[1:]
            if d[1] in '97':
                return d
    return None


# ---------- шаблоны сообщений ----------

DEFAULT_TEMPLATES = [
    {
        'id': 'nosite',
        'title': 'Нет сайта',
        'body': ('Здравствуйте! Нашёл «{название}» в 2ГИС — у вас отличный рейтинг {рейтинг}, '
                 'но нет своего сайта. Клиенты из поиска уходят к тем, у кого он есть.\n\n'
                 'Я делаю сайты для местного бизнеса: с онлайн-записью, ценами и контактами, за 3–5 дней. '
                 'Могу бесплатно показать, как мог бы выглядеть ваш. Интересно?'),
    },
    {
        'id': 'social',
        'title': 'Только соцсети',
        'body': ('Здравствуйте! Вижу, что «{название}» ведёт клиентов через соцсети. '
                 'Сайт с прайсом и кнопкой записи обычно добавляет заявок из Яндекса и 2ГИС.\n\n'
                 'Сделаю такой под ваш {категория} за несколько дней. Показать пример?'),
    },
    {
        'id': 'template',
        'title': 'Сайт на конструкторе',
        'body': ('Здравствуйте! Посмотрел ваш сайт — он на конструкторе, и в поиске его почти не видно. '
                 'Могу сделать нормальный быстрый сайт для «{название}», который будет приводить клиентов. '
                 'Скинуть пару примеров?'),
    },
]

TEMPLATE_VARS = {
    '{название}': 'name', '{категория}': 'category', '{город}': 'city',
    '{адрес}': 'address', '{рейтинг}': 'rating', '{отзывов}': 'reviews',
}


def render_template(body: str, lead: Dict[str, Any]) -> str:
    out = body
    for var, key in TEMPLATE_VARS.items():
        val = lead.get(key)
        if key == 'rating' and isinstance(val, (int, float)):
            val = ('%.1f' % val).replace('.', ',')
        if key == 'category' and val:
            val = str(val).split(',')[0].strip().lower()
        out = out.replace(var, str(val) if val not in (None, '') else '')
    out = re.sub(r'[ \t]+([,.!?])', r'\1', out)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out.strip()


def chat_link(lead: Dict[str, Any], channel: str, text: str = '') -> Optional[str]:
    """Ссылка, открывающая чат с организацией. Для WhatsApp текст подставляется сам,
    в остальные мессенджеры его надо вставить из буфера."""
    first = lambda k: (lead.get(k) or '').split(',')[0].strip() or None  # noqa: E731
    q = urllib.parse.quote(text) if text else ''
    if channel == 'whatsapp':
        num = None
        wa = first('whatsapp')
        if wa:
            m = re.search(r'(?:wa\.me/|phone=)\+?(\d{10,15})', wa)
            num = m.group(1) if m else None
            if not num:
                return wa
        num = num or mobile_digits(lead.get('phone'))
        if not num:
            return None
        return 'https://wa.me/%s%s' % (num, ('?text=' + q) if q else '')
    if channel == 'telegram':
        return first('telegram')
    if channel == 'viber':
        v = first('viber')
        if v:
            return v
        num = mobile_digits(lead.get('phone'))
        return 'viber://chat?number=%2B' + num if num else None
    if channel == 'instagram':
        return first('instagram')
    if channel == 'vkontakte':
        return first('vkontakte')
    if channel == 'website':
        return first('website')
    if channel == 'url':
        return first('url')
    return None


# ---------- база ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id          TEXT PRIMARY KEY,
  data        TEXT NOT NULL,
  name        TEXT,
  city        TEXT,
  category    TEXT,
  query       TEXT,
  kind        TEXT,
  rating      REAL,
  in_base     INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'new',
  note        TEXT NOT NULL DEFAULT '',
  found_at    REAL NOT NULL,
  updated_at  REAL NOT NULL,
  contacted_at REAL
);
CREATE INDEX IF NOT EXISTS leads_base ON leads(in_base);
CREATE TABLE IF NOT EXISTS templates (
  id     TEXT PRIMARY KEY,
  title  TEXT NOT NULL,
  body   TEXT NOT NULL,
  pos    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

PUBLIC_KEYS = ('name', 'category', 'address', 'city', 'district', 'phone', 'whatsapp', 'telegram',
               'email', 'website', 'vkontakte', 'instagram', 'viber', 'odnoklassniki', 'youtube',
               'rating', 'reviews', 'schedule', 'branches', 'coords', 'url', 'yandex')


def _lead_id(rec: Dict[str, Any]) -> str:
    if rec.get('_id'):
        return str(rec['_id'])
    key = '%s|%s' % ((rec.get('name') or '').lower(), (rec.get('address') or '').lower())
    return 'h' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]


class LeadStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.executescript(_SCHEMA)
            seeded = self._db.execute("SELECT v FROM meta WHERE k='templates_seeded'").fetchone()
            if not seeded:
                for i, t in enumerate(DEFAULT_TEMPLATES):
                    self._db.execute('INSERT OR IGNORE INTO templates(id,title,body,pos) VALUES (?,?,?,?)',
                                     (t['id'], t['title'], t['body'], i))
                self._db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('templates_seeded','1')")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ----- запись -----
    def save_parsed(self, rec: Dict[str, Any], query: Optional[str] = None) -> None:
        """Найденная организация → база. Повторная находка обновляет контакты,
        но не трогает статус, заметку и то, что лид уже в базе."""
        data = {k: rec.get(k) for k in PUBLIC_KEYS}
        city = rec.get('city') or ((rec.get('_places') or [None])[0])
        data['city'] = city
        now = time.time()
        lid = _lead_id(rec)
        with self._lock:
            self._db.execute(
                """INSERT INTO leads(id,data,name,city,category,query,kind,rating,found_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data, name=excluded.name,
                     city=excluded.city, category=excluded.category, kind=excluded.kind,
                     rating=excluded.rating, query=COALESCE(excluded.query, leads.query),
                     updated_at=excluded.updated_at""",
                (lid, json.dumps(data, ensure_ascii=False), data.get('name'), city,
                 data.get('category'), query, classify(data), data.get('rating'), now, now))

    def add_to_base(self, ids: List[str], on: bool = True) -> int:
        with self._lock:
            cur = self._db.executemany('UPDATE leads SET in_base=?, updated_at=? WHERE id=?',
                                       [(1 if on else 0, time.time(), i) for i in ids])
            return cur.rowcount

    def set_status(self, ids: List[str], status: str) -> int:
        if status not in STATUSES:
            raise ValueError('Неизвестный статус')
        now = time.time()
        with self._lock:
            cur = self._db.executemany(
                """UPDATE leads SET status=?, in_base=1, updated_at=?,
                   contacted_at=CASE WHEN ?='contacted' THEN ? ELSE contacted_at END WHERE id=?""",
                [(status, now, status, now, i) for i in ids])
            return cur.rowcount

    def set_note(self, lid: str, note: str) -> None:
        with self._lock:
            self._db.execute('UPDATE leads SET note=?, updated_at=? WHERE id=?', (note[:2000], time.time(), lid))

    def delete(self, ids: List[str]) -> int:
        with self._lock:
            cur = self._db.executemany('DELETE FROM leads WHERE id=?', [(i,) for i in ids])
            return cur.rowcount

    def clear_parsed(self) -> int:
        """Очистить вкладку «Парсер» (лиды, не добавленные в базу)."""
        with self._lock:
            return self._db.execute('DELETE FROM leads WHERE in_base=0').rowcount

    # ----- чтение -----
    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = json.loads(r['data'])
        d.update({'id': r['id'], 'kind': r['kind'], 'in_base': bool(r['in_base']), 'status': r['status'],
                  'note': r['note'], 'query': r['query'], 'found_at': r['found_at'],
                  'contacted_at': r['contacted_at'], 'fresh': (time.time() - r['found_at']) < 86400})
        d['has_wa'] = bool(d.get('whatsapp') or mobile_digits(d.get('phone')))
        return d

    def get(self, lid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
        return self._row(r) if r else None

    def get_many(self, ids: List[str]) -> List[Dict[str, Any]]:
        out = []
        for i in ids:
            x = self.get(i)
            if x:
                out.append(x)
        return out

    def query(self, tab: str = 'parser', search: str = '', city: str = '', kind: str = '',
              status: str = '', sort: str = 'new', offset: int = 0, limit: int = 200) -> Dict[str, Any]:
        where, args = ['in_base=?'], [1 if tab == 'base' else 0]
        if city:
            where.append('city=?')
            args.append(city)
        if kind == 'nosite_any':
            where.append("kind IN ('nosite','social','template')")
        elif kind:
            where.append('kind=?')
            args.append(kind)
        if status and tab == 'base':
            where.append('status=?')
            args.append(status)
        if search:
            where.append("(lower(name) LIKE ? OR lower(category) LIKE ? OR lower(data) LIKE ?)")
            s = '%' + search.lower().strip() + '%'
            args += [s, s, s]
        order = {
            'new': 'found_at DESC',
            'rating': 'COALESCE(rating,0) DESC, found_at DESC',
            'name': 'name COLLATE NOCASE ASC',
            'updated': 'updated_at DESC',
        }.get(sort, 'found_at DESC')
        sql_where = ' AND '.join(where)
        with self._lock:
            total = self._db.execute('SELECT COUNT(*) FROM leads WHERE ' + sql_where, args).fetchone()[0]
            ids = [r[0] for r in self._db.execute(
                'SELECT id FROM leads WHERE %s ORDER BY %s' % (sql_where, order), args).fetchall()]
            rows = self._db.execute('SELECT * FROM leads WHERE %s ORDER BY %s LIMIT ? OFFSET ?'
                                    % (sql_where, order), args + [limit, offset]).fetchall()
        return {'items': [self._row(r) for r in rows], 'total': total, 'ids': ids}

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            q = self._db.execute
            total = q('SELECT COUNT(*) FROM leads').fetchone()[0]
            base = q('SELECT COUNT(*) FROM leads WHERE in_base=1').fetchone()[0]
            kinds = {r[0]: r[1] for r in q('SELECT kind, COUNT(*) FROM leads WHERE in_base=0 GROUP BY kind')}
            bkinds = {r[0]: r[1] for r in q('SELECT kind, COUNT(*) FROM leads WHERE in_base=1 GROUP BY kind')}
            statuses = {r[0]: r[1] for r in q('SELECT status, COUNT(*) FROM leads WHERE in_base=1 GROUP BY status')}
            cities = [r[0] for r in q("SELECT DISTINCT city FROM leads WHERE city IS NOT NULL AND city<>'' "
                                     "ORDER BY city")]
        return {'total': total, 'base': base, 'parser': total - base,
                'kinds': kinds, 'base_kinds': bkinds, 'statuses': statuses, 'cities': cities}

    # ----- шаблоны -----
    def templates(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute('SELECT id,title,body FROM templates ORDER BY pos, rowid').fetchall()
        return [dict(r) for r in rows]

    def save_template(self, tid: Optional[str], title: str, body: str) -> Dict[str, Any]:
        title = (title or '').strip()[:80] or 'Без названия'
        body = (body or '').strip()[:4000]
        with self._lock:
            if not tid:
                tid = 't%d' % int(time.time() * 1000)
                pos = self._db.execute('SELECT COALESCE(MAX(pos),0)+1 FROM templates').fetchone()[0]
                self._db.execute('INSERT INTO templates(id,title,body,pos) VALUES (?,?,?,?)', (tid, title, body, pos))
            else:
                self._db.execute('UPDATE templates SET title=?, body=? WHERE id=?', (title, body, tid))
        return {'id': tid, 'title': title, 'body': body}

    def delete_template(self, tid: str) -> None:
        with self._lock:
            self._db.execute('DELETE FROM templates WHERE id=?', (tid,))

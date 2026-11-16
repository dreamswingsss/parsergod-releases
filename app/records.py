"""Превращение JSON-документа 2ГИС в плоскую строку для Excel.

Ядро отдаёт «сырые» ответы API каталога; здесь из них достаются нужные поля,
нормализуются телефоны и ссылки.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from parser_2gis.writer.models import CatalogItem

# Все поля, которые умеет выгружать программа: key -> (заголовок в Excel, группа).
# Порядок здесь = порядок колонок в таблице.
FIELDS: Dict[str, Dict[str, str]] = {
    'name':          {'title': 'Название',        'group': 'info'},
    'category':      {'title': 'Категория',       'group': 'info'},
    'site_kind':     {'title': 'Тип сайта',       'group': 'info'},
    'address':       {'title': 'Адрес',           'group': 'info'},
    'city':          {'title': 'Город',           'group': 'info'},
    'district':      {'title': 'Район',           'group': 'info'},
    'phone':         {'title': 'Телефон',         'group': 'contact'},
    'whatsapp':      {'title': 'WhatsApp',        'group': 'contact'},
    'telegram':      {'title': 'Telegram',        'group': 'contact'},
    'email':         {'title': 'E-mail',          'group': 'contact'},
    'website':       {'title': 'Сайт',            'group': 'contact'},
    'vkontakte':     {'title': 'ВКонтакте',       'group': 'contact'},
    'instagram':     {'title': 'Instagram',       'group': 'contact'},
    'viber':         {'title': 'Viber',           'group': 'contact'},
    'odnoklassniki': {'title': 'Одноклассники',   'group': 'contact'},
    'youtube':       {'title': 'YouTube',         'group': 'contact'},
    'rating':        {'title': 'Рейтинг',         'group': 'info'},
    'reviews':       {'title': 'Отзывов',         'group': 'info'},
    'schedule':      {'title': 'Часы работы',     'group': 'info'},
    'branches':      {'title': 'Филиалов в сети', 'group': 'info'},
    'coords':        {'title': 'Координаты',      'group': 'info'},
    'url':           {'title': 'Ссылка на карточку',     'group': 'info'},
    'yandex':        {'title': 'Яндекс Карты',    'group': 'info'},
    'sources':       {'title': 'Источник',        'group': 'info'},  # режим «2ГИС + Яндекс»
    'price':         {'title': 'Цена',            'group': 'info'},  # Авито
    'snippet':       {'title': 'Описание',        'group': 'info'},  # Авито
}

CONTACT_KEYS = [k for k, v in FIELDS.items() if v['group'] == 'contact']
LINK_CONTACTS = ['website', 'vkontakte', 'instagram', 'telegram', 'whatsapp',
                 'viber', 'odnoklassniki', 'youtube']

# Конструкторы и «сайты-визитки»: ссылка есть, но нормального сайта нет.
TEMPLATE_HOSTS = (
    'taplink.', 'tilda.ws', 'tilda.cc', 'wixsite.com', 'wix.com', 'ukit.me', 'nethouse.ru', 'flexbe.',
    'mssg.me', 'hipolink.', 'linktr.ee', 'clients.site', 'business.site', 'turbo.site', 'ucoz.', 'narod.ru',
    'jimdo', 'webflow.io', 'readymag.', 'tobiz.', 'lpmotor.', 'yclients.com', 'dikidi.', 'linkin.bio',
    'site123.', 'tb.ru', 'wordpress.com', 'blogspot.', 'vk.link',
)
# Соцсети и мессенджеры, которые 2ГИС иногда кладёт в поле «Сайт».
SOCIAL_HOSTS = (
    'vk.com', 'vk.ru', 'instagram.com', 'instagr.am', 't.me', 'telegram.me', 'wa.me', 'whatsapp.com',
    'ok.ru', 'facebook.com', 'youtube.com', 'tiktok.com', 'viber.', 'dzen.ru', 'avito.ru', 'max.ru',
)
SITE_KINDS = {'nosite': 'Без сайта', 'social': 'Только соцсети', 'template': 'Шаблон', 'site': 'Есть сайт'}
_SOCIAL_FIELDS = ('instagram', 'vkontakte', 'telegram', 'whatsapp', 'viber', 'odnoklassniki', 'youtube')


def _host(url: str) -> str:
    u = url.strip().lower()
    if not re.match(r'^[a-z]+://', u):
        u = 'https://' + u
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    host = (p.hostname or '')
    host = host[4:] if host.startswith('www.') else host
    return host + (p.path or '')


def site_kind(rec: Dict[str, Any]) -> str:
    """'Без сайта' / 'Только соцсети' / 'Шаблон' / 'Есть сайт' — по полю «Сайт» и соцсетям."""
    kinds = set()
    for s in (rec.get('website') or '').split(','):
        if not s.strip():
            continue
        h = _host(s)
        kinds.add('social' if any(n in h for n in SOCIAL_HOSTS)
                  else 'template' if any(n in h for n in TEMPLATE_HOSTS) else 'site')
    if 'site' in kinds:
        key = 'site'
    elif 'template' in kinds:
        key = 'template'
    elif 'social' in kinds or any(rec.get(f) for f in _SOCIAL_FIELDS):
        key = 'social'
    else:
        key = 'nosite'
    return SITE_KINDS[key]


_DAYS = (('Mon', 'Пн'), ('Tue', 'Вт'), ('Wed', 'Ср'), ('Thu', 'Чт'),
         ('Fri', 'Пт'), ('Sat', 'Сб'), ('Sun', 'Вс'))


def format_phone(raw: str) -> str:
    """'+7 (343) 300‒12‒34' / '+73433001234' -> '+7 (343) 300-12-34'."""
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 11 and digits[0] in '78':
        d = '7' + digits[1:]
        return '+%s (%s) %s-%s-%s' % (d[0], d[1:4], d[4:7], d[7:9], d[9:11])
    if digits:
        return '+' + digits if (raw or '').strip().startswith('+') else digits
    return (raw or '').strip()


def _unwrap_redirect(value: str) -> str:
    """2ГИС иногда заворачивает ссылки в link.2gis.ru/...?<настоящий адрес>."""
    if 'link.2gis' in value and '?' in value:
        target = urllib.parse.unquote(value.split('?', 1)[1])
        m = re.search(r'https?://\S+', target)
        if m:
            return m.group(0)
    return value


def _clean_link(kind: str, *candidates: Optional[str]) -> Optional[str]:
    for cand in candidates:
        if not cand:
            continue
        val = _unwrap_redirect(cand.strip())
        if kind == 'whatsapp':
            val = val.split('?')[0]
        if 'link.2gis' in val:
            continue  # обёртку раскрыть не удалось — пробуем следующий вариант
        if val.startswith(('http://', 'https://')):
            return val
        if re.match(r'^[\w.-]+\.[a-z]{2,}(/.*)?$', val, re.I):
            return 'https://' + val
    # Последний шанс: вернуть хоть что-то читаемое
    for cand in candidates:
        if cand:
            return cand.strip()
    return None


def _schedule(item: CatalogItem) -> Optional[str]:
    sch = item.schedule
    if not sch:
        return None
    if sch.is_24x7:
        return 'Круглосуточно'
    parts = []
    for key, ru in _DAYS:
        day = getattr(sch, key)
        if day and day.working_hours:
            hours = ', '.join('%s–%s' % (h.from_, h.to) for h in day.working_hours)
            parts.append('%s %s' % (ru, hours))
    # Склеиваем одинаковые дни подряд: «Пн–Пт 09:00–20:00; Сб–Вс 10:00–18:00»
    merged: List[List[str]] = []
    for p in parts:
        ru, hours = p.split(' ', 1)
        if merged and merged[-1][2] == hours:
            merged[-1][1] = ru
        else:
            merged.append([ru, ru, hours])
    return '; '.join(('%s–%s' % (a, b) if a != b else a) + ' ' + h for a, b, h in merged) or None


def yandex_maps_link(name: Optional[str], place: Optional[str],
                     lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[str]:
    """Ссылка на поиск этой организации в Яндекс Картах (по названию, городу и точке на карте)."""
    if not name:
        return None
    text = urllib.parse.quote(' '.join(x for x in (name, place) if x))
    if lat is not None and lon is not None:
        return 'https://yandex.ru/maps/?ll=%.6f,%.6f&z=17&text=%s' % (lon, lat, text)
    return 'https://yandex.ru/maps/?text=%s' % text


_PHOTO_RE = re.compile(r'https?://[\w.-]*photo\.2gis\.com/[^\s"\'<>]+', re.I)


def _photos(raw: Any, limit: int = 6) -> List[str]:
    """Фото заведения, если 2ГИС отдал их в карточке (ссылки на photo.2gis.com)."""
    try:
        text = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return []
    out: List[str] = []
    for u in _PHOTO_RE.findall(text):
        u = u.replace('\\/', '/').rstrip('\\')
        u = re.sub(r'_\d+x\d+(?=\.\w+$)', '', u)  # превью -> исходный размер, если адрес это позволяет
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def parse_doc(doc: Any) -> Optional[Dict[str, Any]]:
    """Документ API -> словарь {field_key: значение} плюс служебный '_id'."""
    try:
        if 'error' in doc.get('meta', {}):
            return None
        raw = doc['result']['items'][0]
        item = CatalogItem(**raw)
    except (KeyError, IndexError, TypeError, AttributeError, ValidationError):
        return None

    if item.name_ex:
        name = item.name_ex.primary
    else:
        name = item.name
    if not name:
        return None

    rec: Dict[str, Any] = {k: None for k in FIELDS}
    rec['_id'] = item.id.split('_')[0] if item.id else None
    rec['_org'] = item.org.id if item.org else None
    rec['_alias'] = item.city_alias
    # Все административные единицы (город, посёлок, район…) — для фильтра по населённому пункту.
    rec['_places'] = [d.name for d in item.adm_div if getattr(d, 'name', None)]
    rec['name'] = name

    primary = [r.name for r in item.rubrics if r.kind == 'primary'] or [r.name for r in item.rubrics]
    rec['category'] = ', '.join(primary[:3]) or (item.name_ex.extension if item.name_ex else None)
    addr = item.address_name
    if addr and item.address_comment:
        addr = '%s (%s)' % (addr, item.address_comment)
    rec['address'] = addr
    for div in item.adm_div:
        if div.type == 'city':
            rec['city'] = div.name
        elif div.type == 'district':
            rec['district'] = div.name
    if item.reviews:
        rec['rating'] = item.reviews.general_rating
        rec['reviews'] = item.reviews.general_review_count
    rec['schedule'] = _schedule(item)
    if item.org and item.org.branch_count:
        rec['branches'] = item.org.branch_count
    if item.point:
        rec['coords'] = '%.6f, %.6f' % (item.point.lat, item.point.lon)
    rec['url'] = item.url
    place = rec['city'] or (rec['_places'][0] if rec['_places'] else None)
    if item.point:
        rec['yandex'] = yandex_maps_link(name, place, item.point.lat, item.point.lon)
    else:
        rec['yandex'] = yandex_maps_link(name, place)

    multi: Dict[str, List[str]] = {k: [] for k in CONTACT_KEYS}
    for group in item.contact_groups:
        for c in group.contacts:
            if c.type == 'phone':
                val = format_phone(c.text or c.value)
            elif c.type == 'email':
                val = (c.value or '').strip()
            elif c.type in LINK_CONTACTS:
                if c.type == 'website':
                    val = _clean_link(c.type, c.value, c.url, c.text)
                else:
                    val = _clean_link(c.type, c.url, c.value, c.text)
            else:
                continue
            if val and val not in multi[c.type]:
                multi[c.type].append(val)
    for k, vals in multi.items():
        rec[k] = ', '.join(vals) if vals else None
    rec['site_kind'] = site_kind(rec)
    rec['_photos'] = _photos(raw)
    return rec

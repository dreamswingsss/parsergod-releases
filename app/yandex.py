"""Сбор организаций с Яндекс Карт.

Как работает: открываем поиск yandex.ru/maps/?text=<запрос город> в браузере,
прокручиваем список результатов и забираем данные из ответов сервера
(/maps/api/search) и из начального состояния страницы (script.state-view).
Организации в этих JSON ищем обходом дерева — так парсер меньше зависит от
мелких изменений формата у Яндекса.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, Iterable, List, Optional

from parser_2gis.chrome import ChromeRemote, ChromeOptions

from .records import FIELDS, format_phone

SEARCH_RE = r'https://yandex\.[a-z.]+/maps/api/search'

_CAPTCHA_JS = r'''(function(){
  var t = document.body ? document.body.innerText : '';
  return /showcaptcha|smartcaptcha|checkcaptcha/.test(location.href) ||
         !!document.querySelector('.CheckboxCaptcha, .AdvancedCaptcha, .SmartCaptcha') ||
         /не робот|подтвердите, что запросы отправляли вы/i.test(t.slice(0, 2000));
})()'''

_STATE_JS = r'''(function(){
  var s = document.querySelector('script.state-view') ||
          document.querySelector('script[type="application/json"][class*="state"]');
  return s ? s.textContent : '';
})()'''

# Прокрутка списка результатов. Возвращает: высоту списка, число карточек, признак конца.
_SCROLL_JS = r'''(function(){
  var el = document.querySelector('.search-list-view .scroll__container') ||
           document.querySelector('.sidebar-view .scroll__container') ||
           document.querySelector('.scroll__container');
  var n = document.querySelectorAll('.search-snippet-view').length;
  var end = !!document.querySelector('.add-business-view, .search-list-view__add-business');
  if (!el) return JSON.stringify({h: 0, n: n, end: end, ok: false});
  el.scrollTop = el.scrollHeight;
  return JSON.stringify({h: el.scrollHeight, n: n, end: end, ok: true});
})()'''

# Ссылки на карточки в списке результатов + прокрутка списка.
_LIST_JS = r"""(function(){
  var links = {};
  document.querySelectorAll('a[href*="/maps/org/"]').forEach(function(a){
    var h = a.getAttribute('href') || '';
    var m = h.match(/\/maps\/org\/(?:[^\/?#]+\/)?(\d{5,})/);
    if (m) links[m[1]] = h.split('?')[0].split('#')[0];
  });
  var el = document.querySelector('.search-list-view .scroll__container') ||
           document.querySelector('.sidebar-view .scroll__container') ||
           document.querySelector('.scroll__container');
  if (el) el.scrollTop = el.scrollHeight;
  var end = !!document.querySelector('.add-business-view, .search-list-view__add-business');
  return JSON.stringify({links: links, end: end, ok: !!el});
})()"""

# Читает карточку организации тем же браузером (куки, проверка пройдена) и отдаёт JSON state-view.
_FETCH_JS = r"""(function(u){
  try {
    var x = new XMLHttpRequest(); x.open('GET', u, false); x.send(null);
    var t = x.responseText || '';
    var m = t.match(/<script type="application\/json" class="state-view">([^<]*)<\/script>/);
    if (!m) return JSON.stringify({err: 'nostate', status: x.status,
      captcha: /showcaptcha|smartcaptcha|checkcaptcha/.test((x.responseURL || '') + t.slice(0, 4000))});
    return m[1];
  } catch (e) { return JSON.stringify({err: String(e)}); }
})(%s)"""

_SOCIAL = {
    'vkontakte': 'vkontakte', 'vk': 'vkontakte', 'telegram': 'telegram', 'whatsapp': 'whatsapp',
    'instagram': 'instagram', 'youtube': 'youtube', 'ok': 'odnoklassniki',
    'odnoklassniki': 'odnoklassniki', 'viber': 'viber',
}
_HOST_SOCIAL = (('vk.com', 'vkontakte'), ('vk.ru', 'vkontakte'), ('t.me', 'telegram'), ('wa.me', 'whatsapp'),
                ('whatsapp', 'whatsapp'), ('instagram.com', 'instagram'), ('youtube.com', 'youtube'),
                ('ok.ru', 'odnoklassniki'), ('viber', 'viber'))


def search_url(text: str, domain: str = 'ru') -> str:
    return 'https://yandex.%s/maps/?text=%s' % (domain, urllib.parse.quote(text, safe=''))


def iter_orgs(node: Any, depth: int = 0) -> Iterable[Dict[str, Any]]:
    """Все словари, похожие на карточку организации, в произвольном JSON."""
    if depth > 40:
        return
    if isinstance(node, dict):
        if node.get('title') and (node.get('address') or node.get('fullAddress')) \
                and (node.get('id') or node.get('coordinates') or node.get('uri')):
            if node.get('type') in (None, 'business', 'org', 'organization'):
                yield node
                return
        for v in node.values():
            yield from iter_orgs(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from iter_orgs(v, depth + 1)


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        for k in ('text', 'value', 'number', 'formatted', 'name', 'href', 'url'):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
    return None


def org_to_record(o: Dict[str, Any]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {k: None for k in FIELDS}
    oid = str(o.get('id') or o.get('oid') or '') or None
    rec['_id'] = ('y' + oid) if oid else None
    chain = o.get('chain') or {}
    rec['_org'] = ('y' + str(chain.get('id'))) if isinstance(chain, dict) and chain.get('id') else None
    rec['name'] = _text(o.get('title'))
    cats = [_text(c) for c in (o.get('categories') or [])]
    rec['category'] = ', '.join([c for c in cats if c][:3]) or None
    full = _text(o.get('fullAddress')) or ''
    rec['address'] = _text(o.get('address')) or full or None
    rec['_places'] = [p.strip() for p in re.split(r',', full) if p.strip()]

    phones = []
    for p in o.get('phones') or []:
        t = _text(p)
        if t:
            f = format_phone(t)
            if f not in phones:
                phones.append(f)
    rec['phone'] = ', '.join(phones) or None

    links: Dict[str, List[str]] = {}
    def add(kind: str, url: Optional[str]) -> None:
        if url and url not in links.setdefault(kind, []):
            links[kind].append(url)
    for s in o.get('socialLinks') or []:
        if isinstance(s, dict):
            kind = _SOCIAL.get(str(s.get('type', '')).lower())
            href = _text(s.get('href')) or _text(s.get('url')) or _text(s)
            if not kind and href:
                kind = next((k for h, k in _HOST_SOCIAL if h in href), None)
            if kind and href:
                add(kind, href.split('?')[0] if kind == 'whatsapp' else href)
    for u in o.get('urls') or []:
        href = _text(u)
        if not href:
            continue
        kind = next((k for h, k in _HOST_SOCIAL if h in href), 'website')
        add(kind, href)
    def strip_tracking(u: str) -> str:
        parts = urllib.parse.urlsplit(u)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not (k.lower().startswith('utm_') or k.lower() in ('yclid', 'from', 'ref', 'fbclid', 'gclid'))]
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))
    if 'website' in links:
        links['website'] = [strip_tracking(u) for u in links['website']]
    for kind, vals in links.items():
        if kind in rec:
            rec[kind] = ', '.join(vals)
    emails = [_text(e) for e in (o.get('emails') or [])]
    rec['email'] = ', '.join([e for e in emails if e]) or None

    rd = o.get('ratingData') or {}
    if isinstance(rd, dict):
        if rd.get('ratingValue') is not None:
            try:
                rec['rating'] = round(float(rd['ratingValue']), 1)
            except (TypeError, ValueError):
                pass
        cnt = rd.get('reviewCount', rd.get('ratingCount'))
        if isinstance(cnt, int):
            rec['reviews'] = cnt
    rec['schedule'] = _text(o.get('workingTimeText')) or _text((o.get('workingTime') or {}) if isinstance(o.get('workingTime'), dict) else None)
    coords = o.get('coordinates') or o.get('coords')
    if isinstance(coords, (list, tuple)) and len(coords) == 2:
        rec['coords'] = '%.6f, %.6f' % (float(coords[1]), float(coords[0]))  # Яндекс: [долгота, широта]
    seo = o.get('seoname')
    if oid and seo:
        rec['url'] = 'https://yandex.ru/maps/org/%s/%s/' % (seo, oid)
    elif oid:
        rec['url'] = 'https://yandex.ru/maps/org/%s/' % oid
    return rec


def _norm(s: str) -> str:
    return (s or '').lower().replace('ё', 'е').strip()


def tile_span(z: int, lat: float, w_px: int = 1450, h_px: int = 1000) -> tuple:
    """Размер видимой области карты (градусы долготы, широты) при зуме z."""
    lon = w_px * 360.0 / (256 * 2 ** z)
    lat_span = lon * (h_px / float(w_px)) * math.cos(math.radians(lat))
    return lon, lat_span


class YandexSearch:
    """Сбор на Яндекс Картах. Яндекс ищет только в видимой части карты, поэтому
    город делится на участки, каждый участок ищется отдельно, повторы убираются.
    Участок, где организаций слишком много, дробится на 4 более мелких."""

    START_ZOOM = 14          # ~7×5 км на участок (на широте Урала)
    MAX_ZOOM = 16
    SPLIT_AT = 450           # если участок дал столько и больше — на всякий случай дробим

    def __init__(self, chrome: ChromeOptions, text: str, on_record: Callable[[Dict[str, Any]], None],
                 cancelled: Callable[[], bool], log: Callable[[str, str], None],
                 on_captcha: Callable[[Optional[str]], None], debug: bool = False) -> None:
        self.chrome, self.text = chrome, text
        self.on_record, self.cancelled, self.log, self.on_captcha = on_record, cancelled, log, on_captcha
        self.remote: Optional[ChromeRemote] = None
        self._seen: set = set()
        self.debug = debug

    def dbg(self, msg: str) -> None:
        if self.debug:
            self.log('debug', msg)

    def close(self) -> None:
        if self.remote is not None:
            try:
                self.remote.stop()
            except Exception:
                pass
            self.remote = None

    def _ensure(self) -> ChromeRemote:
        if self.remote is None:
            self.remote = ChromeRemote(chrome_options=self.chrome, response_patterns=[SEARCH_RE])
            self.remote.start()
        return self.remote

    # ---------- разбор ответов ----------
    def _emit_json(self, raw: str) -> tuple:
        """-> (новых, всего организаций в документе)"""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return 0, 0
        new = seen = 0
        for o in iter_orgs(data):
            seen += 1
            key = str(o.get('id') or o.get('title')) + '|' + str(o.get('address'))
            if key in self._seen:
                continue
            self._seen.add(key)
            rec = org_to_record(o)
            if rec.get('name'):
                new += 1
                self.on_record(rec)  # может бросить «лимит достигнут» / «остановлено»
        return new, seen

    def _drain(self) -> tuple:
        cr = self._ensure()
        new = seen = 0
        while True:
            resp = cr.wait_response(SEARCH_RE)
            if not resp:
                return new, seen
            if resp.get('status', 0) >= 0:
                body = cr.get_response_body(resp, timeout=4)
                a, b = self._emit_json(body)
                new += a
                seen += b

    def _discard_pending(self) -> None:
        cr = self._ensure()
        while cr.wait_response(SEARCH_RE):
            pass

    def _wait_captcha(self) -> bool:
        cr = self._ensure()
        try:
            if not cr.execute_script(_CAPTCHA_JS):
                return True
        except Exception:
            return True
        cr.bring_to_front()
        self.on_captcha('Яндекс просит подтвердить, что вы не робот. Пройдите проверку в окне браузера — сбор продолжится сам.')
        self.log('warn', 'Яндекс показал проверку «вы не робот» — ждём, пока вы её пройдёте.')
        t0 = time.time()
        try:
            while time.time() - t0 < 600 and not self.cancelled():
                time.sleep(1.5)
                try:
                    if not cr.execute_script(_CAPTCHA_JS):
                        self.log('ok', 'Проверка пройдена, продолжаю.')
                        time.sleep(2)
                        return True
                except Exception:
                    continue
            return False
        finally:
            self.on_captcha(None)

    def _state(self) -> str:
        cr = self._ensure()
        for _ in range(10):
            try:
                st = cr.execute_script(_STATE_JS)
            except Exception:
                st = ''
            if st:
                return st
            time.sleep(0.7)
        return ''

    # ---------- один участок / одна страница поиска ----------
    def collect(self, url: str, max_idle: int = 4) -> tuple:
        """Открывает страницу поиска и прокручивает список до конца. -> (новых, найдено всего)"""
        cr = self._ensure()
        self._discard_pending()
        cr.navigate(url, referer='https://yandex.ru/maps/', timeout=60)
        time.sleep(2.5)
        if not self._wait_captcha():
            raise RuntimeError('проверка Яндекса не пройдена')
        new, seen = self._emit_json(self._state())
        a, b = self._drain()
        new, seen = new + a, seen + b
        idle, last_n = 0, -1
        for step in range(80):
            if self.cancelled():
                break
            try:
                info = json.loads(cr.execute_script(_SCROLL_JS) or '{}')
            except Exception:
                info = {}
            time.sleep(1.5)
            a, b = self._drain()
            new, seen = new + a, seen + b
            n = info.get('n', 0)
            self.dbg('  scroll %d: list=%s new=%d seen=%d ok=%s end=%s' % (step, n, a, b, info.get('ok'), info.get('end')))
            if not self._wait_captcha():
                break
            if info.get('end') and a == 0 and b == 0:
                break
            idle = idle + 1 if (b == 0 and n == last_n) else 0
            last_n = n
            if idle >= max_idle:
                break
        return new, max(seen, last_n if last_n > 0 else 0)

    # ---------- границы города ----------
    def geocode(self, place: str) -> Optional[tuple]:
        """-> ((lon1, lat1), (lon2, lat2)) — границы населённого пункта по данным Яндекса."""
        cr = self._ensure()
        cr.navigate(search_url(place), referer='https://yandex.ru/', timeout=60)
        time.sleep(2.5)
        if not self._wait_captcha():
            return None
        self._discard_pending()
        data: Any = {}
        try:
            data = json.loads(self._state() or '{}')
        except ValueError:
            pass
        best = None
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if n.get('type') == 'toponym' and n.get('bounds'):
                    b = n['bounds']
                    if best is None or _norm(n.get('title', '')) == _norm(place.split(',')[0]):
                        best = b
                        if _norm(n.get('title', '')) == _norm(place.split(',')[0]):
                            break
                stack.extend(n.values())
            elif isinstance(n, list):
                stack.extend(n)
        if not best:
            return None
        try:
            (x1, y1), (x2, y2) = best
            return (float(x1), float(y1)), (float(x2), float(y2))
        except (TypeError, ValueError):
            return None

    # ---------- весь город: список + карточки (как в GeoLeadScraper) ----------
    def run_city(self, query: str, place: str, max_links: Optional[int] = None) -> int:
        """Открывает поиск «запрос город», листает список и собирает ссылки на карточки,
        затем читает каждую карточку напрямую (state-view) — там телефон, сайт, соцсети.
        Не зависит от перехвата ответов и определения границ города."""
        cr = self._ensure()
        url = search_url('%s %s' % (query, place))
        cr.navigate(url, referer='https://yandex.ru/maps/', timeout=60)
        time.sleep(3)
        if not self._wait_captcha():
            raise RuntimeError('проверка Яндекса не пройдена')
        links: Dict[str, str] = {}
        idle, step = 0, 0
        while step < 150 and not self.cancelled():
            step += 1
            try:
                info = json.loads(cr.execute_script(_LIST_JS) or '{}')
            except Exception:
                info = {}
            before = len(links)
            for oid, href in (info.get('links') or {}).items():
                links.setdefault(oid, href)
            self.dbg('  list %d: ссылок %d (+%d) ok=%s end=%s' % (step, len(links), len(links) - before,
                                                                 info.get('ok'), info.get('end')))
            if max_links and len(links) >= max_links:
                break
            if info.get('end') and len(links) == before:
                break
            idle = idle + 1 if len(links) == before else 0
            if idle >= 6:
                break
            if not self._wait_captcha():
                break
            time.sleep(1.2)
        if not links:
            self.log('warn', 'Яндекс: по «%s %s» список пуст. Если в окне браузера видна выдача — пришлите скрин журнала.' % (query, place))
            return 0
        self.log('info', 'Яндекс: найдено карточек %d, читаю контакты…' % len(links))
        host = 'https://yandex.ru'
        total = 0
        for i, (oid, href) in enumerate(links.items()):
            if self.cancelled():
                break
            full = href if href.startswith('http') else host + href
            org = self._fetch_org(full)
            if not org:
                continue
            key = 'y' + oid
            if key in self._seen:
                continue
            self._seen.add(key)
            rec = org_to_record(org)
            if not rec.get('name'):
                continue
            rec['_id'] = rec.get('_id') or key
            rec['url'] = rec.get('url') or full
            rec['_enriched'] = True   # данные уже из карточки
            total += 1
            self.on_record(rec)       # может бросить «лимит достигнут» / «остановлено»
            if (i + 1) % 25 == 0:
                self.log('info', 'Яндекс: прочитано карточек %d из %d' % (i + 1, len(links)))
            time.sleep(0.25)
        return total

    def _fetch_org(self, url: str, retry: bool = True) -> Optional[Dict[str, Any]]:
        cr = self._ensure()
        try:
            raw = cr.execute_script(_FETCH_JS % json.dumps(url))
        except Exception as e:
            self.dbg('fetch %s: %s' % (url, e))
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, dict) and data.get('err'):
            self.dbg('fetch %s: %s' % (url, data))
            if data.get('captcha') and retry:
                cr.navigate(url, referer='https://yandex.ru/maps/', timeout=60)
                time.sleep(2)
                if self._wait_captcha():
                    return self._fetch_org(url, retry=False)
            return None
        try:
            return data['stack'][0]['results']['items'][0]
        except (KeyError, IndexError, TypeError):
            return next(iter_orgs(data), None)

    # ---------- точечная проверка одной организации ----------
    def lookup(self, name: str, address: str, city: str) -> List[Dict[str, Any]]:
        """Ищет организацию на Яндекс Картах по «название адрес город» и возвращает
        найденные карточки (обычно 1–5). Поиск идёт XHR-запросом из уже открытой вкладки
        yandex.ru — быстро и без перезагрузки страницы."""
        cr = self._ensure()
        if not getattr(self, '_ready', False):
            cr.navigate('https://yandex.ru/maps/', referer='https://yandex.ru/', timeout=60)
            time.sleep(2.5)
            if not self._wait_captcha():
                raise RuntimeError('проверка Яндекса не пройдена')
            self._ready = True
        text = ' '.join(x for x in (name, address, city) if x)
        url = 'https://yandex.ru/maps/?mode=search&text=%s' % urllib.parse.quote(text, safe='')
        try:
            raw = cr.execute_script(_FETCH_JS % json.dumps(url))
            data = json.loads(raw) if raw else {}
        except Exception as e:
            self.dbg('lookup %s: %s' % (text, e))
            return []
        if isinstance(data, dict) and data.get('err'):
            if data.get('captcha'):
                cr.navigate(url, referer='https://yandex.ru/maps/', timeout=60)
                time.sleep(2)
                if self._wait_captcha():
                    try:
                        raw = cr.execute_script(_FETCH_JS % json.dumps(url))
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        return []
            if not isinstance(data, dict) or data.get('err'):
                return []
        return [o for _, o in zip(range(8), iter_orgs(data))]

    # ---------- старый режим: город по участкам (не используется) ----------
    def run_city_tiles(self, query: str, place: str) -> int:
        bounds = self.geocode(place)
        if not bounds:
            self.log('warn', 'Не удалось определить границы «%s» — ищу по видимой области.' % place)
            return self.collect(search_url('%s %s' % (query, place)))[0]
        (x1, y1), (x2, y2) = bounds
        lat_c = (y1 + y2) / 2
        dx, dy = tile_span(self.START_ZOOM, lat_c)
        nx, ny = max(1, math.ceil((x2 - x1) / dx)), max(1, math.ceil((y2 - y1) / dy))
        queue = []
        for i in range(nx):
            for j in range(ny):
                queue.append((x1 + (i + 0.5) * (x2 - x1) / nx, y1 + (j + 0.5) * (y2 - y1) / ny, self.START_ZOOM))
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        queue.sort(key=lambda t: (t[0] - cx) ** 2 + ((t[1] - cy) * 1.8) ** 2)  # от центра к окраинам
        self.log('info', '«%s»: делю на участки (%d), ищу в каждом от центра к окраинам.' % (place, len(queue)))
        total, done = 0, 0
        while queue and not self.cancelled():
            lon, lat, z = queue.pop(0)
            url = 'https://yandex.ru/maps/?ll=%.6f%%2C%.6f&z=%d&text=%s' % (lon, lat, z, urllib.parse.quote(query, safe=''))
            new, found = self.collect(url)
            total += new
            done += 1
            self.dbg('tile z=%d %.4f,%.4f -> new %d, found %d (осталось %d)' % (z, lon, lat, new, found, len(queue)))
            if found >= self.SPLIT_AT and z < self.MAX_ZOOM:
                ddx, ddy = tile_span(z + 1, lat)
                for sx in (-0.5, 0.5):
                    for sy in (-0.5, 0.5):
                        queue.append((lon + sx * ddx, lat + sy * ddy, z + 1))
            if done % 5 == 0:
                self.log('info', 'Участков пройдено: %d, осталось: %d, найдено: %d' % (done, len(queue), len(self._seen)))
        return total

    # ---------- карточка организации (приём из GeoLeadScraper, MIT) ----------
    def enrich(self, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Открывает карточку организации и достаёт полные данные из script.state-view
        (stack[0].results.items[0]). В выдаче поиска у части организаций нет телефона
        или сайта — в карточке они есть. Вызывать ПОСЛЕ сбора участка: уводит вкладку
        со страницы поиска. Возвращает новую запись или None."""
        url = rec.get('url')
        if not url or self.cancelled():
            return None
        cr = self._ensure()
        try:
            self._discard_pending()
            cr.navigate(url, referer='https://yandex.ru/maps/', timeout=60)
            time.sleep(1.8)
            if not self._wait_captcha():
                return None
            raw = self._state()
            data = json.loads(raw) if raw else {}
        except Exception as e:
            self.dbg('enrich %s: %s' % (url, e))
            return None
        org = None
        try:
            org = data['stack'][0]['results']['items'][0]
        except (KeyError, IndexError, TypeError):
            org = next(iter_orgs(data), None)
        if not isinstance(org, dict):
            return None
        out = org_to_record(org)
        return out if out.get('name') else None

    def run(self) -> int:
        """Старый режим: один поиск по тексту (без деления на участки)."""
        return self.collect(search_url(self.text))[0]

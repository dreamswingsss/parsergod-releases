"""Сбор объявлений услуг с Авито.

Открываем поиск www.avito.ru/<город>?q=<запрос> в браузере пользователя (видимом —
Авито не пускает скрытые браузеры), читаем карточки объявлений со страницы и
листаем страницы. Телефоны Авито показывает только после входа — их нет; связь с
исполнителем — по ссылке на объявление (чат Авито).
Селекторы карточек — как в avito-sdk (github.com/eminsk/avito-sdk, MIT).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, Optional

from parser_2gis.chrome import ChromeRemote, ChromeOptions

from .records import FIELDS

_TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
               ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r',
                's', 't', 'u', 'f', 'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))
_SLUGS = {'москва': 'moskva', 'санкт-петербург': 'sankt-peterburg'}


def city_slug(name: str) -> str:
    """'Нижний Новгород' -> 'nizhniy_novgorod' (как в адресах Авито)."""
    n = (name or '').strip().lower()
    if n in _SLUGS:
        return _SLUGS[n]
    out = ''.join(_TR.get(ch, ch) for ch in n)
    out = re.sub(r'\s+', '_', out)
    return re.sub(r'[^a-z0-9_-]', '', out) or 'rossiya'


def search_url(query: str, city: str, page: int = 1) -> str:
    q = {'q': query}
    if page > 1:
        q['p'] = str(page)
    return 'https://www.avito.ru/%s?%s' % (city_slug(city), urllib.parse.urlencode(q))


_PAGE_JS = r"""(function(){
  var out = [];
  document.querySelectorAll('[data-marker="item"], div[data-item-id]').forEach(function(el){
    var a = el.querySelector('a[data-marker="item-title"]') || el.querySelector('a[itemprop="url"]');
    var t = el.querySelector('[itemprop="name"]') || a;
    var pm = el.querySelector('meta[itemprop="price"]');
    var pt = el.querySelector('[data-marker="item-price"]');
    var geo = el.querySelector('[data-marker="item-address"]') || el.querySelector('[class*="geo-root"]');
    var d = el.querySelector('meta[itemprop="description"]') || el.querySelector('[class*="descriptionStep"]') ||
            el.querySelector('[class*="item-description"]');
    out.push({
      id: el.getAttribute('data-item-id') || '',
      title: t ? (t.getAttribute('title') || t.textContent || '').trim() : '',
      href: a ? a.getAttribute('href') : '',
      price: pm ? pm.getAttribute('content') : (pt ? pt.textContent.trim() : ''),
      geo: geo ? geo.textContent.trim() : '',
      desc: d ? (d.getAttribute('content') || d.textContent || '').trim().slice(0, 300) : ''
    });
  });
  var head = (document.title || '') + ' ' + (document.body ? document.body.innerText.slice(0, 2000) : '');
  return JSON.stringify({
    items: out,
    block: /проблема с ip|доступ ограничен|подтвердите, что запросы|не робот/i.test(head),
    notfound: /такой страницы не существует|страница не найдена/i.test(head),
    next: !!document.querySelector('[data-marker="pagination-button/nextPage"]:not([aria-disabled="true"]), a[data-marker="pagination-button/next"]')
  });
})()"""


def item_to_record(it: Dict[str, Any], city: str, query: str) -> Optional[Dict[str, Any]]:
    href = it.get('href') or ''
    iid = it.get('id') or ''
    if not iid:
        m = re.search(r'_(\d{6,})(?:\?|$)', href)
        iid = m.group(1) if m else ''
    if not iid or not it.get('title'):
        return None
    if href.startswith('/'):
        href = 'https://www.avito.ru' + href
    href = href.split('?')[0] or 'https://www.avito.ru/%s' % iid
    rec: Dict[str, Any] = {k: None for k in FIELDS}
    rec['_id'] = 'a' + iid
    rec['_org'] = None
    rec['_places'] = []
    rec['name'] = it['title']
    rec['category'] = query
    rec['city'] = city
    rec['address'] = it.get('geo') or None
    rec['url'] = href
    price = re.sub(r'\s+', ' ', str(it.get('price') or '')).strip()
    if price.isdigit():
        price = '{:,}'.format(int(price)).replace(',', ' ') + ' ₽'
    rec['price'] = price or None
    rec['snippet'] = it.get('desc') or None
    rec['site_kind'] = 'Только Авито'
    rec['_enriched'] = True
    return rec


class AvitoSearch:
    def __init__(self, chrome: ChromeOptions, on_record: Callable[[Dict[str, Any]], None],
                 cancelled: Callable[[], bool], log: Callable[[str, str], None],
                 on_captcha: Callable[[Optional[str]], None]) -> None:
        self.chrome, self.on_record, self.cancelled, self.log, self.on_captcha = \
            chrome, on_record, cancelled, log, on_captcha
        self.remote: Optional[ChromeRemote] = None

    def _ensure(self) -> ChromeRemote:
        if self.remote is None:
            self.remote = ChromeRemote(chrome_options=self.chrome, response_patterns=[])
            self.remote.start()
        return self.remote

    def close(self) -> None:
        if self.remote is not None:
            try:
                self.remote.stop()
            except Exception:
                pass
            self.remote = None

    def _read(self) -> Dict[str, Any]:
        cr = self._ensure()
        for _ in range(8):
            try:
                info = json.loads(cr.execute_script(_PAGE_JS) or '{}')
            except Exception:
                info = {}
            if info.get('items') or info.get('block') or info.get('notfound'):
                return info
            time.sleep(1)
        return info

    def _wait_block(self) -> bool:
        """Авито показал «доступ ограничен» / капчу — ждём, пока человек пройдёт проверку."""
        cr = self._ensure()
        cr.bring_to_front()
        self.on_captcha('Авито просит подтвердить, что вы не робот. Пройдите проверку в окне браузера — сбор продолжится сам.')
        self.log('warn', 'Авито показал проверку — ждём, пока вы её пройдёте.')
        try:
            t0 = time.time()
            while time.time() - t0 < 600 and not self.cancelled():
                time.sleep(2)
                info = self._read()
                if info.get('items') and not info.get('block'):
                    self.log('ok', 'Проверка пройдена, продолжаю.')
                    return True
            return False
        finally:
            self.on_captcha(None)

    def run(self, query: str, city: str, max_pages: int = 30) -> int:
        cr = self._ensure()
        seen: set = set()
        total = 0
        for page in range(1, max_pages + 1):
            if self.cancelled():
                break
            url = search_url(query, city, page)
            cr.navigate(url, referer='https://www.avito.ru/', timeout=60)
            time.sleep(2.5)
            info = self._read()
            if info.get('block'):
                if not self._wait_block():
                    raise RuntimeError('проверка Авито не пройдена')
                info = self._read()
            if info.get('notfound') and page == 1:
                self.log('warn', 'Авито: город «%s» не найден по адресу %s — ищу по всей России с городом в запросе.'
                         % (city, url))
                return self._run_url(lambda p: 'https://www.avito.ru/rossiya?' + urllib.parse.urlencode(
                    dict({'q': '%s %s' % (query, city)}, **({'p': str(p)} if p > 1 else {}))), city, query, max_pages)
            new = 0
            for it in info.get('items') or []:
                rec = item_to_record(it, city, query)
                if not rec or rec['_id'] in seen:
                    continue
                seen.add(rec['_id'])
                new += 1
                total += 1
                self.on_record(rec)  # может бросить «лимит достигнут» / «остановлено»
            self.log('info', 'Авито, страница %d: объявлений %d' % (page, new))
            if not new or not info.get('next'):
                break
            time.sleep(1.5)
        return total

    def _run_url(self, make_url: Callable[[int], str], city: str, query: str, max_pages: int) -> int:
        cr = self._ensure()
        seen: set = set()
        total = 0
        for page in range(1, max_pages + 1):
            if self.cancelled():
                break
            cr.navigate(make_url(page), referer='https://www.avito.ru/', timeout=60)
            time.sleep(2.5)
            info = self._read()
            if info.get('block') and not self._wait_block():
                raise RuntimeError('проверка Авито не пройдена')
            new = 0
            for it in (self._read().get('items') if info.get('block') else info.get('items')) or []:
                rec = item_to_record(it, city, query)
                if not rec or rec['_id'] in seen:
                    continue
                seen.add(rec['_id'])
                new += 1
                total += 1
                self.on_record(rec)
            if not new or not info.get('next'):
                break
            time.sleep(1.5)
        return total

"""Сбор объявлений услуг с Авито.

Открываем поиск www.avito.ru/<город>?q=<запрос> в браузере пользователя (видимом —
Авито не пускает скрытые браузеры), читаем карточки объявлений со страницы и
листаем страницы. Телефоны Авито показывает только после входа — их нет; связь с
исполнителем — по ссылке на объявление (чат Авито).
Селекторы карточек — как в avito-sdk (github.com/eminsk/avito-sdk, MIT).

Защита от блокировок: на объёме Авито начинает отдавать «доступ ограничен / IP»
(rate-limit) или настоящую капчу. Поэтому: листаем через SPA-кнопку «след. страница»
внутри уже открытой вкладки (не перезагружаем документ на каждую страницу — так
фаервол Авито не прогоняется заново), паузы с джиттером, и две разные реакции на
блок — живую капчу проходит человек, IP-лимит пережидаем бэкоффом.
"""
from __future__ import annotations

import json
import random
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
    var geo = el.querySelector('[data-marker="item-address"]') || el.querySelector('[data-marker="item-location"]') ||
              el.querySelector('[class*="geo-address"]') || el.querySelector('[class*="geo-georeferences"]') ||
              el.querySelector('[class*="geo-root"]') || el.querySelector('[class*="geo-"]');
    var d = el.querySelector('meta[itemprop="description"]') || el.querySelector('[data-marker="item-description"]') ||
            el.querySelector('[class*="descriptionStep"]') || el.querySelector('[class*="item-description"]') ||
            el.querySelector('[class*="iva-item-text"]');
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
  // Настоящий виджет капчи (его может пройти человек) vs «мягкий» IP-лимит (проходить нечего).
  var hasWidget = !!document.querySelector(
    'iframe[src*="captcha"], iframe[src*="geetest"], [class*="geetest"], [id*="geetest"], ' +
    'form[action*="captcha"], [data-marker*="captcha"], iframe[src*="firewall"]');
  var ipblock = /проблема с ip|доступ ограничен|слишком много запросов|too many requests|ошибка 429/i.test(head);
  var captcha = hasWidget || /пройдите проверку|подтвердите, что вы не робот|подтвердите, что запросы|я не робот|captcha/i.test(head);
  return JSON.stringify({
    items: out,
    block: ipblock || captcha,
    ipblock: ipblock && !hasWidget,   // IP-лимит без решаемого виджета
    captcha: captcha,                 // есть что проходить человеку
    notfound: /такой страницы не существует|страница не найдена/i.test(head),
    next: !!document.querySelector('[data-marker="pagination-button/nextPage"]:not([aria-disabled="true"]), a[data-marker="pagination-button/next"]')
  });
})()"""

# Клик по кнопке «следующая страница» внутри SPA — Авито дотягивает пачку XHR-запросом,
# документ не перезагружается, фаервол заново не прогоняется.
_CLICK_NEXT_JS = r"""(function(){
  var b = document.querySelector('[data-marker="pagination-button/nextPage"]:not([aria-disabled="true"])')
       || document.querySelector('a[data-marker="pagination-button/next"]');
  if(!b) return false;
  b.scrollIntoView({block:'center'});
  b.click();
  return true;
})()"""

_FIRST_ID_JS = r"""(function(){
  var e = document.querySelector('[data-marker="item"], div[data-item-id]');
  return e ? (e.getAttribute('data-item-id') || (e.querySelector('a[href]')||{}).href || '') : '';
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

    # --- мелкие помощники -------------------------------------------------

    def _jsleep(self, a: float, b: float) -> None:
        """Пауза со случайной длиной в [a, b] секунд, прерываемая отменой."""
        end = time.time() + random.uniform(a, b)
        while time.time() < end and not self.cancelled():
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def _scroll(self) -> None:
        try:
            self._ensure().execute_script(
                "window.scrollTo(0, Math.floor(document.body.scrollHeight*(0.4+Math.random()*0.5)));")
        except Exception:
            pass

    def _first_id(self) -> str:
        try:
            return self._ensure().execute_script(_FIRST_ID_JS) or ''
        except Exception:
            return ''

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

    # --- обработка блокировок --------------------------------------------

    def _wait_captcha(self) -> bool:
        """Настоящая капча — выносим окно вперёд и ждём, пока человек её пройдёт."""
        cr = self._ensure()
        cr.bring_to_front()
        self.on_captcha('Авито просит подтвердить, что вы не робот. Пройдите проверку в окне браузера — сбор продолжится сам.')
        self.log('warn', 'Авито показал капчу — ждём, пока вы её пройдёте.')
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

    def _handle_block(self, reload_url: str, info: Dict[str, Any]) -> bool:
        """Вернуть True, если доступ восстановлен и можно читать дальше."""
        # Есть решаемый виджет — пусть проходит человек.
        if info.get('captcha') and not info.get('ipblock'):
            return self._wait_captcha()
        # «Мягкий» IP-лимит: проходить нечего. Пережидаем бэкоффом и перепроверяем.
        cr = self._ensure()
        for pause in (60, 120):
            if self.cancelled():
                return False
            self.on_captcha('Авито ограничил доступ по IP (слишком много запросов). Пауза %d с — не закрывай окно.' % pause)
            self.log('warn', 'Авито: ограничение по IP. Пауза %d с и повтор.' % pause)
            end = time.time() + pause
            while time.time() < end and not self.cancelled():
                time.sleep(2)
            self.on_captcha(None)
            if self.cancelled():
                return False
            try:
                cr.navigate(reload_url, referer='https://www.avito.ru/', timeout=60)
            except Exception:
                pass
            self._jsleep(3, 6)
            info = self._read()
            if info.get('items') and not info.get('block'):
                self.log('ok', 'Доступ восстановлен, продолжаю.')
                return True
            if info.get('captcha') and not info.get('ipblock'):
                return self._wait_captcha()
        self.log('error', 'Авито продолжает ограничивать доступ по IP. Останавливаю Авито — собранное сохранено. '
                          'Попробуйте позже, уменьшите число запросов за раз или смените сеть.')
        return False

    # --- основной обход ---------------------------------------------------

    def _collect(self, info: Dict[str, Any], seen: set, city: str, query: str) -> int:
        new = 0
        for it in info.get('items') or []:
            rec = item_to_record(it, city, query)
            if not rec or rec['_id'] in seen:
                continue
            seen.add(rec['_id'])
            new += 1
            self.on_record(rec)  # может бросить «лимит достигнут» / «остановлено»
        return new

    def _next_page(self, query: str, city: str, next_page_no: int) -> bool:
        """Перейти на следующую страницу. Сначала клик по SPA-кнопке (без перезагрузки),
        при неудаче — обычная навигация на ?p=N. Вернуть True, если страница сменилась."""
        cr = self._ensure()
        prev = self._first_id()
        self._jsleep(3, 7)
        try:
            clicked = bool(cr.execute_script(_CLICK_NEXT_JS))
        except Exception:
            clicked = False
        if clicked:
            t0 = time.time()
            while time.time() - t0 < 20 and not self.cancelled():
                time.sleep(1)
                fid = self._first_id()
                if fid and fid != prev:
                    self._scroll()
                    return True
            # Клик не сдвинул выдачу — падаем на обычную навигацию.
        try:
            cr.navigate(search_url(query, city, next_page_no), referer='https://www.avito.ru/', timeout=60)
        except Exception:
            return False
        self._jsleep(3, 6)
        self._scroll()
        return True

    def run(self, query: str, city: str, max_pages: int = 30) -> int:
        cr = self._ensure()
        seen: set = set()
        total = 0
        page = 1
        cr.navigate(search_url(query, city, page), referer='https://www.avito.ru/', timeout=60)
        self._jsleep(3, 5)
        self._scroll()
        while page <= max_pages and not self.cancelled():
            info = self._read()
            if info.get('block'):
                if not self._handle_block(search_url(query, city, page), info):
                    raise RuntimeError('доступ к Авито ограничен')
                info = self._read()
            if info.get('notfound') and page == 1:
                self.log('warn', 'Авито: город «%s» не найден — ищу по всей России с городом в запросе.' % city)
                return self._run_url(
                    lambda p: 'https://www.avito.ru/rossiya?' + urllib.parse.urlencode(
                        dict({'q': '%s %s' % (query, city)}, **({'p': str(p)} if p > 1 else {}))),
                    city, query, max_pages)
            new = self._collect(info, seen, city, query)
            total += new
            self.log('info', 'Авито, страница %d: объявлений %d' % (page, new))
            if not new or not info.get('next'):
                break
            if not self._next_page(query, city, page + 1):
                break
            page += 1
        return total

    def _run_url(self, make_url: Callable[[int], str], city: str, query: str, max_pages: int) -> int:
        """Запасной обход по готовым URL (город не нашёлся — ищем по всей России).
        Здесь без SPA-клика: URL строятся снаружи, листаем навигацией, но с джиттером
        и той же обработкой блоков."""
        cr = self._ensure()
        seen: set = set()
        total = 0
        cr.navigate(make_url(1), referer='https://www.avito.ru/', timeout=60)
        self._jsleep(3, 5)
        self._scroll()
        for page in range(1, max_pages + 1):
            if self.cancelled():
                break
            info = self._read()
            if info.get('block'):
                if not self._handle_block(make_url(page), info):
                    raise RuntimeError('доступ к Авито ограничен')
                info = self._read()
            new = self._collect(info, seen, city, query)
            total += new
            if not new or not info.get('next'):
                break
            cr.navigate(make_url(page + 1), referer='https://www.avito.ru/', timeout=60)
            self._jsleep(3, 7)
            self._scroll()
        return total

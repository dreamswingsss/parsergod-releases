"""Load textual reviews from public organisation cards."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import urljoin, urlsplit

import requests

from .audit import checked_at
from .enrich import UA

MAX_REVIEWS = 30
PER_SOURCE = 20
MAX_HTML = 5_000_000
MAP_2GIS = '2ГИС'
MAP_YANDEX = 'Яндекс Карты'
HOSTS_2GIS = {'2gis.ru', '2gis.kz', '2gis.by', '2gis.uz', '2gis.kg', '2gis.az', '2gis.ae',
              '2gis.com', '2gis.ge', '2gis.am'}
HOSTS_YANDEX = {'yandex.ru', 'yandex.com', 'yandex.kz', 'yandex.by', 'yandex.uz'}

# Both the server-rendered review page and the browser use this same list of
# text markers. A container or an arbitrary quotation is not a review.
TEXT_CLASSES = {
    'business-review-view__body-text',
    'business-review-view__review-text',
    'review-text',
    'reviewText',
}
TEXT_MARKERS = {'review-text', 'review-body', 'reviewText', 'reviewBody'}

_STRUCTURED_START = re.compile(
    r'\{(?=\s*"(?:@type|id|reviewId|reviewBody|rating|score|text|author|authorId|dateCreated|datePublished)"\s*:)'
)
_BLOCKED = re.compile(
    r'<title[^>]*>[^<]*(?:captcha|капча|вы робот|вы не робот|smartcaptcha)|'
    r'<form[^>]+(?:captcha|showcaptcha)', re.I
)


class Unavailable(ValueError):
    """An unavailable, restricted or unreadable public source."""


def _card_url(value: str, provider: str) -> str:
    value = value.split(', ', 1)[0].strip()
    try:
        url = urlsplit(value)
        host = (url.hostname or '').removeprefix('www.')
        if url.scheme not in ('http', 'https') or url.username or url.password or url.port not in (None, 80, 443):
            return ''
    except ValueError:
        return ''
    if provider == MAP_2GIS and host in HOSTS_2GIS:
        m = re.match(r'^((?:/[\w-]+)?/firm/[0-9]{8,})(?:/|$)', url.path)
        if m:
            return 'https://' + host + m[1] + '/tab/reviews'
    if provider == MAP_YANDEX and host in HOSTS_YANDEX:
        m = re.match(r'^(/maps/(?:[0-9]+/[^/]+/)?org/(?:[^/]+/)?[0-9]{5,})(?:/|$)', url.path)
        if m:
            return 'https://' + host + m[1] + '/reviews/'
    return ''


def _same_card(left: str, right: str, provider: str) -> bool:
    def number(value: str) -> str:
        canonical = _card_url(value, provider)
        m = re.search(r'/([0-9]+)/(?:tab/)?reviews', canonical)
        return m[1] if m else ''

    number_left = number(left)
    return bool(number_left and number_left == number(right))


def sources_for(lead: dict) -> list[dict]:
    return [
        {'name': provider, 'url': url}
        for provider, field in ((MAP_2GIS, 'url'), (MAP_YANDEX, 'yandex'))
        if (url := _card_url(lead.get(field) or '', provider))
    ]


def _review(text, rating=None) -> dict | None:
    if not isinstance(text, str):
        return None
    text = ' '.join(text.split())
    if len(text) < 5 or not any(c.isalpha() for c in text):
        return None
    try:
        stars = float(str(rating).replace(',', '.'))
        stars = stars if 1 <= stars <= 5 else None
    except (ValueError, TypeError):
        stars = None
    return {'text': text[:1200], 'rating': stars}


def _structured(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    rating = data.get('rating') or data.get('score') or data.get('stars') or data.get('reviewRating')
    if isinstance(rating, dict):
        rating = rating.get('ratingValue') or rating.get('value') or rating.get('rating')
    body = data.get('reviewBody')
    if not isinstance(body, str):
        body = data.get('text') or data.get('reviewText')
        if not rating or not (data.get('id') or data.get('reviewId') or data.get('author')
                              or data.get('authorId') or data.get('dateCreated')
                              or data.get('datePublished') or data.get('createdAt')):
            return None
    return _review(body, rating)


class _Markup(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.scripts: list[str] = []
        self._script = False
        self._stack: list[str] = []
        self._parts: list[str] = []
        self._containers: list[tuple[str, set[str], int]] = []
        self._seen_cards: set[int] = set()
        self._index = 0

    def handle_starttag(self, tag, attrs) -> None:
        attr = dict(attrs)
        classes = set((attr.get('class') or '').split())
        if tag == 'script' and not attr.get('src'):
            self._script = True
            return
        if tag not in self.VOID:
            self._index += 1
            self._containers.append((tag, classes, self._index))
        if self._stack:
            if tag == 'br':
                self._parts.append(' ')
            if tag not in self.VOID:
                self._stack.append(tag)
            return
        twogis_card = next((i for _, css, i in reversed(self._containers)
                            if '_1k5soqfl' in css), None) if '_49x36f' in classes else None
        if tag not in self.VOID and (
            attr.get('itemprop') == 'reviewBody'
            or attr.get('data-testid') in TEXT_MARKERS
            or attr.get('data-test-id') in TEXT_MARKERS
            or TEXT_CLASSES.intersection(classes)
            or (twogis_card is not None and twogis_card not in self._seen_cards)
        ):
            if twogis_card is not None:
                self._seen_cards.add(twogis_card)
            self._parts = []
            self._stack = [tag]

    def handle_endtag(self, tag) -> None:
        if tag == 'script':
            self._script = False
        if self._containers and tag == self._containers[-1][0]:
            self._containers.pop()
        if self._stack and tag == self._stack[-1]:
            self._stack.pop()
            if not self._stack:
                value = _review(''.join(self._parts))
                if value:
                    self.items.append(value)

    def handle_data(self, data) -> None:
        if self._script and len(data) >= 15:
            self.scripts.append(data)
        elif self._stack:
            self._parts.append(data)


def _key(text: str) -> str:
    return re.sub(r'\W+', ' ', text.casefold().replace('ё', 'е')).strip()


def _unique(rows) -> list[dict]:
    unique: dict[str, dict] = {}
    for row in rows:
        key = _key(row['text'])
        if key and (key not in unique or (not unique[key]['rating'] and row['rating'])):
            unique[key] = row
    return list(unique.values())[:PER_SOURCE]


def extract_html(html: str) -> list[dict]:
    reader = _Markup()
    reader.feed(html[:MAX_HTML])
    reader.close()
    decoder = json.JSONDecoder()
    found: list[dict] = []
    for script in reader.scripts:
        for index, match in enumerate(_STRUCTURED_START.finditer(script)):
            if index >= 1500 or len(found) >= PER_SOURCE * 3:
                break
            try:
                data, _ = decoder.raw_decode(script[match.start():match.start() + 16000])
            except (ValueError, RecursionError):
                continue
            result = _structured(data)
            if result:
                found.append(result)
    # Prefer visible text, but enrich exact matches with rating from the JSON.
    return _unique([*reader.items, *found])


def _download(source: dict) -> list[dict]:
    origin = source['url']
    provider = source['name']
    url = origin
    deadline = time.monotonic() + 9
    with requests.Session() as session:
        for _ in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            with session.get(url, headers={'User-Agent': UA, 'Accept-Language': 'ru-RU,ru;q=0.9'},
                             timeout=(min(4, remaining), min(6, remaining)), stream=True,
                             allow_redirects=False) as response:
                if response.is_redirect:
                    next_url = urljoin(url, response.headers.get('Location', ''))
                    if not _same_card(next_url, origin, provider):
                        raise Unavailable('Карта перенаправила на другую страницу.')
                    url = next_url
                    continue
                if response.status_code in (401, 403, 429):
                    raise Unavailable('Источник ограничил доступ к отзывам.')
                if response.status_code == 404:
                    raise Unavailable('Карточка больше не открывается.')
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) >= MAX_HTML or time.monotonic() >= deadline:
                        break
                html = data.decode('utf-8', errors='replace')
                if _BLOCKED.search(html):
                    raise Unavailable('Карта запросила проверку в браузере.')
                return extract_html(html)
    return []


def _browser_reviews(source: dict, binary: str) -> list[dict]:
    import pychrome
    from pychrome.exceptions import PyChromeException
    from parser_2gis.chrome.browser import ChromeBrowser
    from parser_2gis.chrome.options import ChromeOptions

    chrome = ChromeBrowser(ChromeOptions(binary_path=binary, headless=True, silent_browser=True))
    tab = None
    deadline = time.monotonic() + 17
    try:
        with requests.Session() as session:
            session.trust_env = False
            endpoint = f'http://127.0.0.1:{chrome.remote_port}/json/new'
            while time.monotonic() < deadline:
                try:
                    with session.put(endpoint, timeout=1) as response:
                        response.raise_for_status()
                        tab = pychrome.Tab(**response.json())
                        break
                except requests.RequestException:
                    time.sleep(.2)
            if tab is None:
                return []
            tab.start()
            navigation = tab.Page.navigate(url=source['url'], _timeout=5)
            if navigation.get('errorText'):
                return []
            result = []
            last_size = 0
            # Dynamic pages add reviews after initial document render.
            # Size changes avoid reparsing an unchanged page while it loads.
            while time.monotonic() < deadline:
                response = tab.Runtime.evaluate(
                    expression='({url: location.href, html: document.documentElement?.outerHTML.slice(0, 5000000) || ""})',
                    returnByValue=True, _timeout=3,
                )
                page = response.get('result', {}).get('value') or {}
                html = page.get('html', '')
                if _BLOCKED.search(html):
                    raise Unavailable('Карта запросила проверку в браузере.')
                if _same_card(page.get('url', ''), source['url'], source['name']) and len(html) != last_size:
                    last_size = len(html)
                    result = extract_html(html)
                    if result:
                        return result
                time.sleep(.7)
            return result
    except PyChromeException:
        return []
    finally:
        try:
            if tab is not None:
                tab.stop()
        finally:
            chrome.close()


def _load(source: dict, browser: str) -> tuple[dict, list[dict]]:
    try:
        try:
            reviews = _download(source)
        except requests.RequestException:
            reviews = []
        if not reviews and browser:
            reviews = _browser_reviews(source, browser)
        if reviews:
            return {**source, 'count': len(reviews)}, reviews
        return {**source, 'count': 0, 'error': 'На странице нет доступных текстов отзывов.'}, []
    except Unavailable as exc:
        return {**source, 'count': 0, 'error': str(exc)}, []
    except (OSError, ValueError, RuntimeError, requests.RequestException):
        return {**source, 'count': 0, 'error': 'Не удалось прочитать страницу отзывов.'}, []


def collect(lead: dict, browser: str = '') -> dict:
    urls = sources_for(lead)
    if not urls:
        return {'sources': [], 'items': []}
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        results = list(pool.map(lambda source: _load(source, browser), urls))
    unique: dict[str, dict] = {}
    # Interleave both sources so that the limit does not hide the second one.
    for index in range(PER_SOURCE):
        for source, rows in results:
            if index >= len(rows):
                continue
            row = rows[index]
            key = _key(row['text'])
            if key in unique:
                labels = unique[key]['sources']
                if source['name'] not in labels:
                    labels.append(source['name'])
            elif len(unique) < MAX_REVIEWS:
                unique[key] = {**row, 'sources': [source['name']]}
    items = list(unique.values())
    return {
        'sources': [source for source, _ in results],
        'items': items,
        'checked_at': checked_at(),
        'id': sha256(json.dumps(items, ensure_ascii=False).encode()).hexdigest(),
    }

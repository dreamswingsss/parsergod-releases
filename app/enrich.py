"""Дополнительные данные для демо: сайт/соцсеть заведения и страница карточки 2ГИС.

Всё best-effort: если страница не отвечает, закрыта входом или отдаёт пустую
заготовку для JavaScript, просто возвращаем то, что удалось достать.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
MAX_BYTES = 3_000_000
_PRICE_RE = re.compile(r'(?:от\s*)?\d[\d\s]{1,6}(?:[.,]\d{1,2})?\s*(?:₽|руб\.?|р\.)(?=[^\w]|$)', re.I)
_PHOTO_2GIS_RE = re.compile(r'https?:(?:\\?/){2}[\w.-]*photo\.2gis\.com(?:\\?/[^\s"\'<>\\]+)+', re.I)
_SKIP_HOSTS = ('instagram.com', 'instagr.am', 'facebook.com', 'wa.me', 'whatsapp.com', 't.me', 'telegram.me')


def _get(url: str, timeout: float = 12) -> Optional[str]:
    import requests
    try:
        r = requests.get(url, headers={'User-Agent': UA, 'Accept-Language': 'ru-RU,ru;q=0.9'},
                         timeout=timeout, stream=True, allow_redirects=True)
        if r.status_code >= 400 or 'html' not in (r.headers.get('Content-Type') or 'text/html'):
            return None
        data = b''
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                break
        enc = r.encoding if r.encoding and r.encoding.lower() != 'iso-8859-1' else (r.apparent_encoding or 'utf-8')
        return data.decode(enc, errors='replace')
    except Exception:
        return None


class _Page(HTMLParser):
    """Достаёт из HTML заголовок, описание, заголовки разделов, абзацы и картинки."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title = ''
        self.meta: Dict[str, str] = {}
        self.blocks: List[str] = []
        self.images: List[str] = []
        self._tag: List[str] = []
        self._buf: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'svg', 'template'):
            self._skip += 1
        elif tag == 'meta':
            k = (a.get('property') or a.get('name') or '').lower()
            if k in ('og:title', 'og:description', 'description', 'og:image') and a.get('content'):
                self.meta.setdefault(k, a['content'].strip())
        elif tag == 'img':
            src = a.get('data-original') or a.get('data-src') or a.get('src') or ''
            if src and not src.startswith('data:') and not re.search(r'(logo|icon|sprite|pixel|counter|\.svg)', src, re.I):
                self.images.append(urllib.parse.urljoin(self.base, src))
        if tag in ('h1', 'h2', 'h3', 'p', 'li', 'td', 'title', 'button', 'a', 'span', 'div'):
            self._flush()
        self._tag.append(tag)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg', 'template') and self._skip:
            self._skip -= 1
        if tag in ('h1', 'h2', 'h3', 'p', 'li', 'td', 'title', 'div'):
            self._flush(tag == 'title')
        if self._tag:
            self._tag.pop()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def _flush(self, is_title: bool = False):
        text = re.sub(r'\s+', ' ', ''.join(self._buf)).strip()
        self._buf = []
        if not text:
            return
        if is_title and not self.title:
            self.title = text
        elif 2 < len(text) < 600 and text not in self.blocks:
            self.blocks.append(text)


def fetch_site(url: str) -> Dict[str, Any]:
    """Сайт, Taplink или страница VK заведения → тексты, цены, картинки."""
    url = (url or '').split(', ')[0].strip()
    if not url.startswith(('http://', 'https://')):
        return {}
    host = urllib.parse.urlsplit(url).hostname or ''
    if any(h in host for h in _SKIP_HOSTS):
        return {}
    page = _get(url)
    if not page:
        return {'url': url, 'error': 'страница не открылась'}
    p = _Page(url)
    try:
        p.feed(page)
        p.close()
    except Exception:
        pass
    blocks = [b for b in p.blocks if not re.search(r'(cookie|javascript|©|политик|войти|регистрац)', b, re.I)]
    prices = []
    for b in blocks:
        if _PRICE_RE.search(b) and len(b) < 160 and b not in prices:
            prices.append(b)
    images = []
    if p.meta.get('og:image'):
        images.append(urllib.parse.urljoin(url, p.meta['og:image']))
    for i in p.images:
        if i not in images and i.startswith('http'):
            images.append(i)
    text, total = [], 0
    for b in blocks:
        if b in prices:
            continue
        total += len(b)
        if total > 4000:
            break
        text.append(b)
    return {
        'url': url,
        'title': p.meta.get('og:title') or p.title,
        'description': p.meta.get('og:description') or p.meta.get('description') or '',
        'text': text[:60],
        'prices': prices[:40],
        'images': images[:8],
    }


def fetch_2gis(card_url: str) -> Dict[str, Any]:
    """Страница карточки 2ГИС: фото, описание и отзывы, если они есть в HTML."""
    if not card_url or '2gis' not in card_url:
        return {}
    out: Dict[str, Any] = {'photos': [], 'reviews': [], 'description': ''}
    page = _get(card_url.rstrip('/'))
    if page:
        for u in _PHOTO_2GIS_RE.findall(page):
            u = u.replace('\\/', '/')
            if u not in out['photos']:
                out['photos'].append(u)
        for m in re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.){40,900})"', page):
            try:
                t = json.loads('"%s"' % m.group(1))
            except ValueError:
                continue
            t = re.sub(r'\s+', ' ', html.unescape(t)).strip()
            if re.search(r'[а-яё]{3,}', t, re.I) and t not in out['reviews']:
                out['reviews'].append(t)
        m = re.search(r'<meta[^>]+(?:name|property)="(?:og:)?description"[^>]+content="([^"]+)"', page)
        if m:
            out['description'] = html.unescape(m.group(1)).strip()
    reviews_page = _get(card_url.rstrip('/') + '/tab/reviews')
    if reviews_page:
        for m in re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.){40,900})"', reviews_page):
            try:
                t = re.sub(r'\s+', ' ', json.loads('"%s"' % m.group(1))).strip()
            except ValueError:
                continue
            if re.search(r'[а-яё]{3,}', t, re.I) and t not in out['reviews']:
                out['reviews'].append(t)
    out['photos'] = out['photos'][:8]
    out['reviews'] = out['reviews'][:6]
    return out


def gather(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Всё, что удалось собрать для демо, + короткая сводка для окна."""
    sources = []
    for key in ('website', 'vkontakte'):
        for u in (lead.get(key) or '').split(', ')[:1]:
            if u:
                sources.append(u)
    sites = [s for s in (fetch_site(u) for u in sources) if s]
    gis = fetch_2gis(lead.get('url') or '')
    photos: List[str] = list(lead.get('_photos') or [])
    for u in gis.get('photos', []) + [i for s in sites for i in s.get('images', [])]:
        if u not in photos:
            photos.append(u)
    summary = []
    for s in sites:
        host = (urllib.parse.urlsplit(s.get('url', '')).hostname or '').removeprefix('www.')
        if s.get('error'):
            summary.append('%s — не открылся' % host)
        else:
            summary.append('%s — %d абзацев, %d цен, %d фото' % (host, len(s.get('text', [])), len(s.get('prices', [])),
                                                              len(s.get('images', []))))
    summary.append('2ГИС — %d фото, %d отзывов' % (len(gis.get('photos', [])) + len(lead.get('_photos') or []),
                                                 len(gis.get('reviews', []))))
    return {'sites': sites, 'gis': gis, 'photos': photos[:10], 'summary': summary}

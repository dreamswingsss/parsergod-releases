"""Демо-сайт для лида: одностраничник из данных 2ГИС и публикация на Vercel.

Сайт собирается по шаблону без ИИ: название, рейтинг, адрес, часы, телефон,
мессенджеры и типовые услуги для сферы бизнеса. Тексты можно поправить в окне
программы или переписать в Claude (промпт копируется, ответ — JSON).
Публикация — на Vercel пользователя по его токену; без токена демо сохраняется
одним HTML-файлом, который можно отправить клиенту.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

# ---------- тексты по сферам ----------

# (ключевые слова в категории/запросе) -> пресет
PRESETS: List[Tuple[Tuple[str, ...], Dict[str, Any]]] = [
    (('барбер', 'мужск'), {
        'headline': 'Мужские стрижки и бритьё без лишних слов',
        'services': ['Мужская стрижка', 'Стрижка машинкой', 'Оформление бороды', 'Королевское бритьё', 'Стрижка + борода', 'Детская стрижка'],
        'cta': 'Записаться', 'theme': 'dark'}),
    (('маникюр', 'ногт', 'nail'), {
        'headline': 'Аккуратный маникюр, который держится',
        'services': ['Маникюр с покрытием', 'Педикюр', 'Наращивание', 'Дизайн ногтей', 'Снятие покрытия', 'SPA-уход для рук'],
        'cta': 'Записаться', 'theme': 'soft'}),
    (('бров', 'ресниц', 'lash', 'brow'), {
        'headline': 'Брови и ресницы, которые подчёркивают взгляд',
        'services': ['Коррекция бровей', 'Окрашивание бровей', 'Ламинирование бровей', 'Ламинирование ресниц', 'Наращивание ресниц', 'Перманентный макияж'],
        'cta': 'Записаться', 'theme': 'warm'}),
    (('космет', 'эпиляц', 'spa', 'спа', 'массаж'), {
        'headline': 'Уход, после которого хочется вернуться',
        'services': ['Чистка лица', 'Пилинги', 'Уходовые процедуры', 'Массаж лица', 'Эпиляция', 'Консультация косметолога'],
        'cta': 'Записаться', 'theme': 'warm'}),
    (('тату', 'пирсинг'), {
        'headline': 'Тату и пирсинг в спокойной атмосфере',
        'services': ['Тату по эскизу мастера', 'Индивидуальный эскиз', 'Мини-тату', 'Перекрытие старых работ', 'Пирсинг', 'Консультация'],
        'cta': 'Обсудить эскиз', 'theme': 'dark'}),
    (('салон красоты', 'парикмах', 'красот', 'стрижк', 'визаж', 'макияж'), {
        'headline': 'Ваш идеальный образ — в одном месте',
        'services': ['Женская стрижка', 'Окрашивание', 'Укладка', 'Уход для волос', 'Макияж', 'Маникюр'],
        'cta': 'Записаться', 'theme': 'warm'}),
    (('стомат', 'зуб'), {
        'headline': 'Лечим зубы бережно и без страха',
        'services': ['Консультация и осмотр', 'Лечение кариеса', 'Профгигиена', 'Отбеливание', 'Имплантация', 'Детская стоматология'],
        'cta': 'Записаться на приём', 'theme': 'fresh'}),
    (('медицин', 'клиник', 'врач', 'психолог', 'логопед', 'остеопат'), {
        'headline': 'Внимательные специалисты рядом с домом',
        'services': ['Консультация специалиста', 'Диагностика', 'Лечение', 'Повторный приём', 'Анализы', 'Программы наблюдения'],
        'cta': 'Записаться на приём', 'theme': 'fresh'}),
    (('ветерин', 'ветклин', 'груминг', 'зоо', 'кинолог'), {
        'headline': 'Заботимся о питомцах как о своих',
        'services': ['Приём ветеринара', 'Вакцинация', 'Груминг и стрижка', 'Гигиенические процедуры', 'Чипирование', 'Консультация'],
        'cta': 'Записаться', 'theme': 'fresh'}),
    (('автосервис', 'шиномонтаж', 'автомойк', 'детейлинг', 'кузов', 'автоэлектр', 'тонировк', 'авто'), {
        'headline': 'Сделаем быстро и объясним, что сделали',
        'services': ['Диагностика', 'Техобслуживание', 'Ремонт подвески', 'Шиномонтаж', 'Мойка и детейлинг', 'Автоэлектрика'],
        'cta': 'Записаться на сервис', 'theme': 'dark'}),
    (('кофейн', 'кофе', 'пекар', 'кондитер'), {
        'headline': 'Свежий кофе и выпечка каждый день',
        'services': ['Кофе с собой', 'Авторские напитки', 'Свежая выпечка', 'Десерты', 'Завтраки', 'Торты на заказ'],
        'cta': 'Заказать', 'theme': 'warm'}),
    (('кафе', 'ресторан', 'бар', 'пицц', 'суши', 'роллы', 'шаурм', 'столов', 'доставк', 'бургер'), {
        'headline': 'Вкусно, сытно и всегда свежее',
        'services': ['Меню на каждый день', 'Бизнес-ланч', 'Доставка', 'Блюда с собой', 'Банкеты и праздники', 'Детское меню'],
        'cta': 'Заказать', 'theme': 'warm'}),
    (('фитнес', 'тренаж', 'йог', 'танц', 'единобор', 'бассейн', 'пилатес', 'спорт'), {
        'headline': 'Тренировки, на которые хочется приходить',
        'services': ['Пробное занятие', 'Групповые тренировки', 'Персональный тренер', 'Абонементы', 'Программы для новичков', 'Детские группы'],
        'cta': 'Записаться на пробное', 'theme': 'dark'}),
    (('ремонт', 'потолк', 'окна', 'двер', 'мебель', 'кухн', 'клининг', 'сантехн', 'электрик'), {
        'headline': 'Делаем под ключ, по смете и в срок',
        'services': ['Бесплатный замер', 'Расчёт сметы', 'Работы под ключ', 'Материалы со скидкой', 'Гарантия на работы', 'Выезд мастера'],
        'cta': 'Вызвать замерщика', 'theme': 'fresh'}),
    (('фото', 'видео', 'ведущ', 'праздн', 'аниматор', 'кейтер', 'цвет'), {
        'headline': 'Сделаем ваш праздник незабываемым',
        'services': ['Фотосессии', 'Видеосъёмка', 'Организация праздников', 'Оформление', 'Пакетные предложения', 'Консультация'],
        'cta': 'Узнать стоимость', 'theme': 'warm'}),
    (('курс', 'репетит', 'школ', 'обучен', 'детский центр'), {
        'headline': 'Учиться понятно и интересно',
        'services': ['Пробное занятие', 'Индивидуальные занятия', 'Группы', 'Онлайн-формат', 'Подготовка к экзаменам', 'Программы для детей'],
        'cta': 'Записаться на пробное', 'theme': 'fresh'}),
]
DEFAULT_PRESET = {
    'headline': 'Работаем для вас каждый день',
    'services': ['Консультация', 'Основные услуги', 'Индивидуальный подход', 'Удобная запись', 'Гарантия качества', 'Честные цены'],
    'cta': 'Связаться', 'theme': 'fresh',
}

THEMES = {
    'fresh':   {'title': 'Свежий',  'bg': '#F4F8F7', 'card': '#FFFFFF', 'ink': '#13231F', 'ink2': '#5C6F69',
                'accent': '#0F7B63', 'accent_ink': '#FFFFFF', 'line': '#DCE8E4', 'font': 'grotesk'},
    'warm':    {'title': 'Тёплый',  'bg': '#FBF7F2', 'card': '#FFFFFF', 'ink': '#2B211A', 'ink2': '#7A6B5E',
                'accent': '#8A5A3B', 'accent_ink': '#FFFFFF', 'line': '#EADFD3', 'font': 'serif'},
    'dark':    {'title': 'Тёмный',  'bg': '#0E0E10', 'card': '#18181B', 'ink': '#F4F4F5', 'ink2': '#A1A1AA',
                'accent': '#F4F4F5', 'accent_ink': '#0E0E10', 'line': '#2A2A2F', 'font': 'grotesk'},
    'soft':    {'title': 'Нежный',  'bg': '#FDF6F7', 'card': '#FFFFFF', 'ink': '#3A2430', 'ink2': '#8C6C7A',
                'accent': '#C2577E', 'accent_ink': '#FFFFFF', 'line': '#F2DEE5', 'font': 'rounded'},
    'premium': {'title': 'Премиум', 'bg': '#111111', 'card': '#1A1A1A', 'ink': '#F3EEE4', 'ink2': '#A99F8E',
                'accent': '#C9A45C', 'accent_ink': '#111111', 'line': '#2E2A24', 'font': 'serif'},
}

FONTS = {
    'system':  {'title': 'Системный', 'css': '-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif', 'link': ''},
    'grotesk': {'title': 'Гротеск', 'css': '"Manrope",-apple-system,"Segoe UI",Roboto,Arial,sans-serif',
                'link': 'Manrope:wght@400;600;700;800'},
    'serif':   {'title': 'С засечками', 'css': '"Playfair Display",Georgia,"Times New Roman",serif',
                'link': 'Playfair+Display:wght@500;700'},
    'rounded': {'title': 'Округлый', 'css': '"Nunito",ui-rounded,"Segoe UI",Roboto,Arial,sans-serif',
                'link': 'Nunito:wght@400;600;800'},
}

LAYOUTS = {'split': 'Текст + фото', 'cover': 'Фото на весь экран', 'center': 'По центру'}

ACCENTS = ['#0F7B63', '#2563EB', '#7C3AED', '#C2577E', '#DC2626', '#EA580C', '#8A5A3B', '#C9A45C', '#111111']

SECTION_TITLES = {
    'offer': 'Акция',
    'services': 'Услуги и цены',
    'benefits': 'Почему выбирают нас',
    'gallery': 'Фото',
    'about': 'О нас',
    'reviews': 'Отзывы',
    'steps': 'Как записаться',
    'faq': 'Вопросы и ответы',
    'contacts': 'Контакты и карта',
}
SECTION_ORDER = list(SECTION_TITLES)

_PRICE_SPLIT = re.compile(r'\s*[—–:-]?\s*((?:от\s*)?\d[\d\s]{0,6}(?:[.,]\d{1,2})?\s*(?:₽|руб\.?|р\.))\s*$', re.I)


def _preset(lead: Dict[str, Any]) -> Dict[str, Any]:
    hay = ' '.join(str(lead.get(k) or '') for k in ('category', '_query', 'name')).lower().replace('ё', 'е')
    for keys, p in PRESETS:
        if any(k in hay for k in keys):
            return p
    return DEFAULT_PRESET


def quoted(name: str) -> str:
    """«Название», но без двойных кавычек, если они уже есть в самом названии."""
    return name if re.search(r'[«"“]', name or '') else '«%s»' % name


def _first(v: Optional[str]) -> str:
    return (v or '').split(', ')[0].strip()


def _rating(lead: Dict[str, Any]) -> str:
    r = lead.get('rating')
    return ('%.1f' % r).replace('.', ',') if isinstance(r, (int, float)) and r else ''


def _messengers(lead: Dict[str, Any]) -> str:
    names = [t for k, t in (('whatsapp', 'WhatsApp'), ('telegram', 'Telegram')) if lead.get(k)]
    if not names and _wa_link(lead):
        names = ['WhatsApp']
    return ' или '.join(names)


def services_from_prices(lines: List[str]) -> List[Dict[str, str]]:
    """'Женская стрижка — от 1 500 ₽' -> {'name': 'Женская стрижка', 'price': 'от 1 500 ₽'}."""
    out = []
    for ln in lines:
        m = _PRICE_SPLIT.search(ln)
        if not m:
            continue
        name = ln[:m.start()].strip(' —–:-·')
        if 2 <= len(name) <= 80:
            out.append({'name': name, 'price': re.sub(r'\s+', ' ', m.group(1)).strip(), 'note': ''})
    return out[:12]


def default_content(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Стартовый демо: тексты, разделы и оформление из данных 2ГИС и пресета сферы."""
    p = _preset(lead)
    cat = _first(lead.get('category')).lower()
    city = lead.get('city') or ''
    rating = _rating(lead)
    reviews_n = lead.get('reviews')
    phone = _first(lead.get('phone'))
    sched = lead.get('schedule') or ''
    addr = lead.get('address') or ''
    msg = _messengers(lead)

    sub_parts = [x for x in (cat.capitalize(), city) if x]
    if rating:
        sub_parts.append('рейтинг %s в 2ГИС' % rating + (' по %s отзывам' % reviews_n if reviews_n else ''))

    benefits = []
    if rating:
        benefits.append({'title': '★ %s в 2ГИС' % rating,
                         'text': '%s отзывов от реальных клиентов' % reviews_n if reviews_n else 'Высокая оценка клиентов'})
    if msg:
        benefits.append({'title': 'Запись в пару кликов', 'text': 'Напишите в %s — ответим и подберём время' % msg})
    if sched:
        benefits.append({'title': 'Удобный график', 'text': sched})
    if addr:
        benefits.append({'title': 'Легко найти', 'text': addr})
    if len(benefits) < 3:
        benefits.append({'title': 'Индивидуальный подход', 'text': 'Подскажем и подберём вариант под вашу задачу'})

    how = ('Напишите в %s' % msg) if msg else ('Позвоните %s' % phone if phone else 'Свяжитесь с нами')
    faq = [{'q': 'Как записаться?', 'a': how + ('. Или позвоните: %s.' % phone if msg and phone else '.')}]
    if addr:
        faq.append({'q': 'Где вы находитесь?', 'a': addr + (', ' + city if city and city not in addr else '')})
    if sched:
        faq.append({'q': 'Когда вы работаете?', 'a': sched})
    faq.append({'q': 'Сколько стоит?', 'a': 'Точную стоимость подскажем при записи — зависит от услуги и деталей.'})

    return {
        'headline': p['headline'],
        'subtitle': ' · '.join(sub_parts) or addr,
        'about': ('%s — %s%s. Приходите — покажем, почему нас выбирают и возвращаются снова.'
                  % (quoted(lead.get('name') or 'Мы'), cat or 'место, где ценят ваше время',
                     ', ' + addr if addr else '')),
        'cta': p['cta'],
        'theme': p['theme'],
        'accent': '',
        'font': '',
        'layout': 'split',
        'photos': [],
        'sections': {
            'offer': {'on': False, 'title': 'Скидка 10% на первый визит',
                      'text': 'Скажите при записи, что нашли нас через сайт'},
            'services': {'on': True, 'title': 'Услуги и цены',
                         'items': [{'name': s, 'price': '', 'note': ''} for s in p['services']]},
            'benefits': {'on': True, 'title': 'Почему выбирают нас', 'items': benefits[:4]},
            'gallery': {'on': True, 'title': 'Фото'},
            'about': {'on': True, 'title': 'О нас'},
            'reviews': {'on': False, 'title': 'Отзывы клиентов', 'items': []},
            'steps': {'on': True, 'title': 'Как записаться', 'items': [
                {'title': how, 'text': 'Ответим в течение рабочего дня'},
                {'title': 'Выберите время', 'text': 'Подстроимся под ваш график'},
                {'title': 'Приходите', 'text': 'Мы уже ждём' + (' — ' + addr if addr else '')}]},
            'faq': {'on': True, 'title': 'Вопросы и ответы', 'items': faq},
            'contacts': {'on': True, 'title': 'Контакты', 'map': True},
        },
    }


def normalize(content: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    """Доводит старое/неполное содержимое демо до текущего формата."""
    c = dict(content or {})
    base = default_content(lead)
    for k in ('headline', 'subtitle', 'about', 'cta', 'theme', 'accent', 'font', 'layout', 'photos'):
        c.setdefault(k, base[k])
    if c.get('theme') not in THEMES:
        c['theme'] = base['theme']
    secs = dict(c.get('sections') or {})
    if 'services' not in secs and isinstance(c.get('services'), list):  # формат 1.1: список строк
        base['sections']['services']['items'] = [{'name': str(s), 'price': '', 'note': ''} for s in c['services'] if s]
    for key, val in base['sections'].items():
        cur = dict(secs.get(key) or {})
        for k2, v2 in val.items():
            cur.setdefault(k2, v2)
        secs[key] = cur
    c['sections'] = secs
    c.pop('services', None)
    return c


def apply_source(content: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    """После «Подтянуть данные»: цены с сайта → услуги, отзывы 2ГИС → раздел «Отзывы»."""
    c = normalize(content, lead)
    src = c.get('source') or {}
    prices = [p for s in src.get('sites') or [] for p in s.get('prices') or []]
    items = services_from_prices(prices)
    if len(items) >= 3:
        c['sections']['services']['items'] = items
    reviews = (src.get('gis') or {}).get('reviews') or []
    if reviews and not c['sections']['reviews'].get('items'):
        c['sections']['reviews']['items'] = [{'text': r[:400], 'author': 'Отзыв из 2ГИС'} for r in reviews[:4]]
        c['sections']['reviews']['on'] = True
    return c


# ---------- промпт для Claude: только тексты (оставлен для совместимости) ----------

def claude_prompt(lead: Dict[str, Any], content: Dict[str, Any]) -> str:
    facts = {k: lead.get(k) for k in ('name', 'category', 'city', 'address', 'rating', 'reviews', 'schedule')
             if lead.get(k) not in (None, '')}
    return ('Ты копирайтер. Перепиши тексты одностраничного сайта местного бизнеса.\nДанные из 2ГИС:\n%s\n\n'
            'Ответь ТОЛЬКО JSON: {"headline": "...", "subtitle": "...", "about": "...", "services": ["..."], "cta": "..."}'
            % json.dumps(facts, ensure_ascii=False, indent=1))


def parse_claude_answer(text: str) -> Dict[str, Any]:
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        raise ValueError('В ответе нет JSON. Скопируйте ответ Claude целиком.')
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ValueError('Не получилось прочитать JSON из ответа Claude.')
    out: Dict[str, Any] = {k: data[k].strip()[:400] for k in ('headline', 'subtitle', 'about', 'cta')
                           if isinstance(data.get(k), str) and data[k].strip()}
    if isinstance(data.get('services'), list):
        out['services'] = [str(s).strip()[:80] for s in data['services'] if str(s).strip()][:9]
    if not out:
        raise ValueError('В JSON нет нужных полей.')
    return out


# ---------- HTML ----------

def _wa_link(lead: Dict[str, Any]) -> str:
    wa = _first(lead.get('whatsapp'))
    if wa.startswith('http'):
        return wa
    for p in (lead.get('phone') or '').split(','):
        d = re.sub(r'\D', '', p)
        if len(d) == 11 and d[0] in '78' and d[1] in '97':
            return 'https://wa.me/7' + d[1:]
    return ''


def _tel(phone: str) -> str:
    d = re.sub(r'\D', '', phone)
    if len(d) == 11 and d[0] == '8':
        d = '7' + d[1:]
    return '+' + d if d else ''


def _hex(color: str) -> Optional[str]:
    return color if re.fullmatch(r'#[0-9a-fA-F]{6}', color or '') else None


def _ink_on(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return '#111111' if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else '#FFFFFF'


def _map_embed(lead: Dict[str, Any]) -> str:
    m = re.match(r'\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', lead.get('coords') or '')
    if not m:
        return ''
    lat, lon = float(m.group(1)), float(m.group(2))
    bbox = '%.5f,%.5f,%.5f,%.5f' % (lon - 0.006, lat - 0.003, lon + 0.006, lat + 0.003)
    return ('https://www.openstreetmap.org/export/embed.html?bbox=%s&layer=mapnik&marker=%.6f,%.6f'
            % (urllib.parse.quote(bbox, safe=','), lat, lon))


def render(lead: Dict[str, Any], content: Dict[str, Any]) -> str:
    if content.get('custom_html'):  # «Свой дизайн» от ИИ / Claude
        return fill_photos(content['custom_html'], [p for p in content.get('photos') or [] if isinstance(p, str)])
    c = normalize(content, lead)
    S = c['sections']
    e = lambda s: html.escape(str(s or ''), quote=True)  # noqa: E731
    on = lambda k: bool(S.get(k, {}).get('on'))  # noqa: E731

    t = dict(THEMES.get(c['theme']) or THEMES['fresh'])
    acc = _hex(c.get('accent') or '')
    if acc:
        t['accent'], t['accent_ink'] = acc, _ink_on(acc)
    font = FONTS.get(c.get('font') or t['font']) or FONTS['system']
    font_link = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                 '<link href="https://fonts.googleapis.com/css2?family=%s&display=swap" rel="stylesheet">'
                 % font['link']) if font['link'] else ''

    name = lead.get('name') or 'Демо'
    phone = _first(lead.get('phone'))
    tel = _tel(phone)
    wa = _wa_link(lead)
    rating = _rating(lead)
    map_url = _first(lead.get('url')) or _first(lead.get('yandex'))
    links = []
    for key, title in (('whatsapp', 'WhatsApp'), ('telegram', 'Telegram'), ('vkontakte', 'ВКонтакте'),
                       ('instagram', 'Instagram'), ('viber', 'Viber')):
        u = wa if key == 'whatsapp' else _first(lead.get(key))
        if u.startswith('http'):
            links.append('<a class="chip" href="%s" target="_blank" rel="noopener">%s</a>' % (e(u), title))
    primary = wa or (('tel:' + tel) if tel else '') or _first(lead.get('telegram')) or '#contacts'
    photos = [p for p in (c.get('photos') or []) if isinstance(p, str)
              and (p.startswith('data:image/') or p.startswith('https://'))][:8]
    cta = e(c.get('cta') or 'Связаться')
    btn = '<a class="btn" href="%s" target="_blank" rel="noopener">%s</a>' % (e(primary), cta)
    call = ('<a class="btn ghost" href="tel:%s">Позвонить</a>' % e(tel)) if tel and not primary.startswith('tel:') else ''
    badge = ('<span class="badge">★ <b>%s</b> в 2ГИС%s</span>'
             % (rating, (' · %s отзывов' % e(lead.get('reviews'))) if lead.get('reviews') else '')) if rating else ''

    # ---- первый экран ----
    layout = c.get('layout') if c.get('layout') in LAYOUTS else 'split'
    hero_text = ('<div class="hero-t">%s<h1>%s</h1><p class="sub">%s</p><div class="actions">%s%s</div></div>'
                 % (badge, e(c.get('headline')), e(c.get('subtitle')), btn, call))
    if layout == 'cover':
        bg = ('background-image:linear-gradient(180deg,rgba(0,0,0,.25),rgba(0,0,0,.7)),url(%s)' % photos[0]) if photos \
            else 'background-image:radial-gradient(1000px 500px at 80% -10%,var(--acc),transparent 60%)'
        hero = '<section class="hero cover" style="%s"><div class="wrap">%s</div></section>' % (bg, hero_text)
    elif layout == 'center':
        hero = '<section class="hero center"><div class="wrap">%s</div></section>' % hero_text
    else:
        side = ('<div class="hero-img" style="background-image:url(%s)"></div>' % photos[0]) if photos else \
            ('<div class="hero-card"><div class="big">%s</div><p>%s</p></div>'
             % (('★ ' + rating) if rating else e(name[:1].upper()),
                'средняя оценка гостей в 2ГИС' if rating else e(_first(lead.get('category')))))
        hero = '<section class="hero split"><div class="wrap grid2">%s%s</div></section>' % (hero_text, side)

    blocks = []

    def sec(key: str, inner: str, cls: str = '') -> None:
        title = S[key].get('title') or SECTION_TITLES[key]
        blocks.append('<section class="block %s" id="%s"><div class="wrap"><h2>%s</h2>%s</div></section>'
                      % (cls, key, e(title), inner))

    if on('offer') and (S['offer'].get('title') or S['offer'].get('text')):
        blocks.append('<section class="block"><div class="wrap"><div class="offer"><div><b>%s</b><p>%s</p></div>%s</div>'
                      '</div></section>' % (e(S['offer'].get('title')), e(S['offer'].get('text')), btn))
    if on('services'):
        items = [i for i in S['services'].get('items') or [] if (i.get('name') or '').strip()]
        if items:
            sec('services', '<div class="svc-list">%s</div>' % ''.join(
                '<div class="svc"><div><b>%s</b>%s</div>%s</div>'
                % (e(i['name']), ('<small>%s</small>' % e(i['note'])) if i.get('note') else '',
                   ('<span class="price">%s</span>' % e(i['price'])) if i.get('price') else '')
                for i in items[:12]))
    if on('benefits'):
        items = [i for i in S['benefits'].get('items') or [] if (i.get('title') or '').strip()]
        if items:
            sec('benefits', '<div class="cards">%s</div>' % ''.join(
                '<div class="card"><b>%s</b><p>%s</p></div>' % (e(i['title']), e(i.get('text'))) for i in items[:6]))
    if on('gallery') and len(photos) > 1:
        sec('gallery', '<div class="gallery">%s</div>' % ''.join(
            '<img src="%s" alt="%s" loading="lazy">' % (e(p), e(name)) for p in photos[1:]))
    if on('about') and c.get('about'):
        sec('about', '<div class="about">%s</div>' % e(c['about']))
    if on('reviews'):
        items = [i for i in S['reviews'].get('items') or [] if (i.get('text') or '').strip()]
        if items:
            head = ('<div class="score"><b>%s</b><span>★★★★★</span><small>%s</small></div>'
                    % (rating, ('%s отзывов в 2ГИС' % e(lead.get('reviews'))) if lead.get('reviews') else '')) if rating else ''
            sec('reviews', head + '<div class="cards">%s</div>' % ''.join(
                '<div class="card review"><p>«%s»</p><small>%s</small></div>' % (e(i['text']), e(i.get('author')))
                for i in items[:6]))
    if on('steps'):
        items = [i for i in S['steps'].get('items') or [] if (i.get('title') or '').strip()]
        if items:
            sec('steps', '<div class="steps">%s</div>' % ''.join(
                '<div class="step"><i>%d</i><b>%s</b><p>%s</p></div>' % (n + 1, e(i['title']), e(i.get('text')))
                for n, i in enumerate(items[:5])))
    if on('faq'):
        items = [i for i in S['faq'].get('items') or [] if (i.get('q') or '').strip()]
        if items:
            sec('faq', '<div class="faq">%s</div>' % ''.join(
                '<details><summary>%s</summary><p>%s</p></details>' % (e(i['q']), e(i.get('a'))) for i in items[:8]))
    if on('contacts'):
        mp = _map_embed(lead) if S['contacts'].get('map', True) else ''
        sched = lead.get('schedule') or 'Уточняйте по телефону'
        inner = ('<div class="contacts"><div class="cinfo">'
                 '<div class="box"><small>Адрес</small>%s%s</div>'
                 '<div class="box"><small>Часы работы</small>%s</div>'
                 '<div class="box"><small>Телефон</small>%s</div>'
                 '<div class="box"><small>Мессенджеры и соцсети</small>%s</div></div>%s</div>'
                 % (e(lead.get('address') or '—'),
                    (' <div class="chips"><a class="chip" href="%s" target="_blank" rel="noopener">Открыть на карте</a></div>'
                     % e(map_url)) if map_url else '',
                    e(sched), ('<a href="tel:%s">%s</a>' % (e(tel), e(phone))) if tel else '—',
                    ('<div class="chips">%s</div>' % ''.join(links)) if links else '—',
                    ('<iframe class="map" src="%s" loading="lazy" title="Карта"></iframe>' % e(mp)) if mp else ''))
        sec('contacts', inner)

    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="{desc}">{font_link}
<style>
:root{{--bg:{bg};--card:{card};--ink:{ink};--ink2:{ink2};--acc:{acc};--acc-ink:{acc_ink};--line:{line};--font:{font}}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 var(--font);-webkit-font-smoothing:antialiased}}
a{{color:inherit}} img{{max-width:100%}}
.wrap{{max-width:1100px;margin:0 auto;padding:0 20px}}
header{{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}}
header .wrap{{display:flex;align-items:center;justify-content:space-between;gap:12px;height:64px}}
.logo{{font-weight:800;font-size:18px;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
nav{{display:flex;gap:18px;font-size:14px;color:var(--ink2)}} nav a{{text-decoration:none}} nav a:hover{{color:var(--ink)}}
.btn{{display:inline-flex;align-items:center;justify-content:center;height:50px;padding:0 24px;border-radius:14px;background:var(--acc);color:var(--acc-ink);font-weight:700;text-decoration:none;white-space:nowrap;border:1px solid var(--acc)}}
.btn.ghost{{background:transparent;color:inherit;border-color:var(--line)}}
header .btn{{height:40px;padding:0 16px;font-size:14px}}
.hero{{padding:64px 0 48px}}
.grid2{{display:grid;grid-template-columns:1.1fr .9fr;gap:40px;align-items:center}}
.hero.center{{text-align:center}} .hero.center .sub{{margin-left:auto;margin-right:auto}} .hero.center .actions{{justify-content:center}}
.hero.cover{{color:#fff;background-size:cover;background-position:center;min-height:78vh;display:flex;align-items:flex-end;padding-bottom:64px}}
.hero.cover .sub{{color:rgba(255,255,255,.85)}} .hero.cover .badge{{background:rgba(0,0,0,.35);color:#fff;border-color:rgba(255,255,255,.25)}}
.hero.cover .btn.ghost{{border-color:rgba(255,255,255,.5)}}
.badge{{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:6px 14px;font-size:14px;color:var(--ink2)}}
.badge b{{color:inherit}}
h1{{font-size:clamp(34px,5.4vw,60px);line-height:1.05;letter-spacing:-.025em;margin:18px 0 16px;font-weight:800}}
.sub{{color:var(--ink2);font-size:19px;margin:0 0 28px;max-width:600px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap}}
.hero-img{{aspect-ratio:4/5;border-radius:26px;background:var(--card) center/cover;border:1px solid var(--line)}}
.hero-card{{border-radius:26px;background:var(--acc);color:var(--acc-ink);padding:36px;aspect-ratio:4/5;display:flex;flex-direction:column;justify-content:flex-end}}
.hero-card .big{{font-size:72px;font-weight:800;letter-spacing:-.03em;line-height:1}}
.hero-card p{{opacity:.8;margin:10px 0 0;font-size:17px}}
.block{{padding:44px 0}}
h2{{font-size:clamp(26px,3.4vw,36px);letter-spacing:-.02em;margin:0 0 22px;font-weight:800}}
.offer{{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;background:var(--acc);color:var(--acc-ink);border-radius:22px;padding:26px 28px}}
.offer b{{font-size:22px}} .offer p{{margin:4px 0 0;opacity:.85}} .offer .btn{{background:var(--acc-ink);color:var(--acc);border-color:var(--acc-ink)}}
.svc-list{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.svc{{display:flex;justify-content:space-between;align-items:center;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px}}
.svc b{{font-weight:700}} .svc small{{display:block;color:var(--ink2);font-size:14px;margin-top:2px}}
.price{{font-weight:800;white-space:nowrap;color:var(--acc)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}}
.card b{{font-size:18px}} .card p{{color:var(--ink2);margin:8px 0 0}}
.review p{{color:var(--ink);margin:0 0 12px;font-size:16px}} .review small{{color:var(--ink2)}}
.score{{display:flex;align-items:baseline;gap:12px;margin:-6px 0 18px}} .score b{{font-size:44px;font-weight:800}}
.score span{{color:var(--acc);letter-spacing:2px}} .score small{{color:var(--ink2)}}
.gallery{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.gallery img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:16px;border:1px solid var(--line)}}
.about{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:30px;font-size:18px;color:var(--ink2)}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.step{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}}
.step i{{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--acc);color:var(--acc-ink);font-style:normal;font-weight:800;margin-bottom:14px}}
.step b{{font-size:18px}} .step p{{color:var(--ink2);margin:6px 0 0}}
.faq details{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:8px}}
.faq summary{{cursor:pointer;font-weight:700;list-style:none}} .faq summary::-webkit-details-marker{{display:none}}
.faq summary::after{{content:"+";float:right;color:var(--acc);font-weight:800}} .faq details[open] summary::after{{content:"–"}}
.faq p{{color:var(--ink2);margin:10px 0 0}}
.contacts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.cinfo{{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-content:start}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}}
.box small{{display:block;color:var(--ink2);font-size:13px;margin-bottom:4px}}
.map{{width:100%;height:100%;min-height:300px;border:1px solid var(--line);border-radius:18px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.chip{{border:1px solid var(--line);border-radius:999px;padding:6px 12px;text-decoration:none;font-size:14px}}
.final{{text-align:center;padding:56px 22px;border-radius:26px;background:var(--acc);color:var(--acc-ink);margin:24px 0}}
.final h2{{margin-bottom:8px}} .final p{{opacity:.85;margin:0 0 22px}}
.final .btn{{background:var(--acc-ink);color:var(--acc);border-color:var(--acc-ink)}}
footer{{color:var(--ink2);font-size:13px;text-align:center;padding:26px 0 96px}}
.float{{position:fixed;left:16px;right:16px;bottom:16px;display:none;z-index:6}}
.float .btn{{width:100%;box-shadow:0 10px 30px rgba(0,0,0,.25)}}
@media (max-width:860px){{.grid2,.contacts,.cinfo,.svc-list{{grid-template-columns:1fr}}.hero{{padding:32px 0 24px}}
.hero-img,.hero-card{{aspect-ratio:16/11}}.gallery{{grid-template-columns:1fr 1fr}}.float{{display:block}}nav,header .btn{{display:none}}}}
</style></head>
<body>
<header><div class="wrap"><div class="logo">{name}</div><nav>{nav}</nav>{head_btn}</div></header>
{hero}
{blocks}
<div class="wrap"><div class="final"><h2>{cta_title}</h2><p>Ответим и подберём удобное время</p>{btn}</div></div>
<footer>{name}{city}<br>Демо-версия сайта</footer>
<div class="float">{btn}</div>
</body></html>""".format(
        title=e(name + (' — ' + _first(lead.get('category')) if lead.get('category') else '')),
        desc=e(c.get('subtitle') or ''), font_link=font_link,
        bg=t['bg'], card=t['card'], ink=t['ink'], ink2=t['ink2'], acc=t['accent'], acc_ink=t['accent_ink'],
        line=t['line'], font=font['css'], name=e(name),
        nav=''.join('<a href="#%s">%s</a>' % (k, e(SECTION_TITLES[k].split(' ')[0]))
                    for k in ('services', 'reviews', 'contacts') if on(k)),
        head_btn=('<a class="btn" href="tel:%s">%s</a>' % (e(tel), e(phone))) if tel else '',
        hero=hero, blocks='\n'.join(blocks), btn=btn, cta_title=e(c.get('cta') or 'Свяжитесь с нами'),
        city=(' · ' + e(lead['city'])) if lead.get('city') else '',
    )


# ---------- slug и Vercel ----------

_TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
               ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't',
                'u', 'f', 'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))


def slugify(text: str, limit: int = 40) -> str:
    s = ''.join(_TR.get(c, c) for c in (text or '').lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    s = re.sub(r'-{2,}', '-', s)
    return (s[:limit].rstrip('-')) or 'demo'


VERCEL_API = 'https://api.vercel.com'


class VercelError(Exception):
    pass


def _vercel(method: str, path: str, token: str, **kw) -> Dict[str, Any]:
    import requests
    try:
        r = requests.request(method, VERCEL_API + path, headers={'Authorization': 'Bearer ' + token},
                             timeout=kw.pop('timeout', 30), **kw)
    except requests.RequestException as e:
        raise VercelError('Нет связи с Vercel: %s' % e.__class__.__name__)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code in (401, 403):
        raise VercelError('Vercel не принял токен. Создайте новый в vercel.com → Account Settings → Tokens.')
    if r.status_code >= 400:
        msg = (data.get('error') or {}).get('message') if isinstance(data.get('error'), dict) else None
        raise VercelError('Vercel: %s' % (msg or 'ошибка %d' % r.status_code))
    return data


def vercel_check(token: str) -> str:
    """Проверяет токен, возвращает имя аккаунта."""
    data = _vercel('GET', '/v2/user', token)
    u = data.get('user') or {}
    return u.get('username') or u.get('email') or 'аккаунт Vercel'


def vercel_publish(token: str, project: str, page: str, timeout: float = 90) -> str:
    """Публикует index.html в проект `project` на Vercel пользователя. Возвращает публичный адрес."""
    body = {
        'name': project,
        'target': 'production',
        'files': [{'file': 'index.html', 'data': page, 'encoding': 'utf-8'}],
        'projectSettings': {'framework': None, 'buildCommand': None, 'installCommand': None, 'outputDirectory': None},
    }
    dep = _vercel('POST', '/v13/deployments?skipAutoDetectionConfirmation=1', token, json=body, timeout=60)
    dep_id, project_id = dep.get('id'), dep.get('projectId') or project
    # Демо должны открываться без входа в Vercel: снимаем защиту деплоев с проекта.
    try:
        _vercel('PATCH', '/v9/projects/%s' % urllib.parse.quote(project_id), token, json={'ssoProtection': None})
    except VercelError:
        pass
    t0 = time.time()
    while dep_id and time.time() - t0 < timeout:
        cur = _vercel('GET', '/v13/deployments/%s' % dep_id, token)
        state = cur.get('readyState') or cur.get('status')
        if state == 'READY':
            dep = cur
            break
        if state in ('ERROR', 'CANCELED'):
            raise VercelError('Vercel не смог опубликовать демо (%s)' % state)
        time.sleep(2)
    aliases = [a for a in (dep.get('alias') or []) if isinstance(a, str)]
    if not aliases:
        try:
            doms = _vercel('GET', '/v9/projects/%s/domains' % urllib.parse.quote(project_id), token)
            aliases = [d.get('name') for d in doms.get('domains') or [] if d.get('name')]
        except VercelError:
            pass
    if aliases:
        best = sorted(aliases, key=lambda a: (not a.endswith('.vercel.app'), len(a)))[0]
        return 'https://' + best
    if dep.get('url'):
        return 'https://' + dep['url']
    raise VercelError('Vercel не вернул адрес демо')


# ---------- «Свой дизайн»: весь сайт делает Claude ----------

PHOTO_TOKEN = '{{PHOTO_%d}}'


def site_prompt(lead: Dict[str, Any], content: Dict[str, Any], wishes: str = '') -> str:
    """Промпт для claude.ai: сверстать одностраничник под заведение по всем собранным данным."""
    facts = {k: lead.get(k) for k in ('name', 'category', 'city', 'address', 'phone', 'schedule', 'rating',
                                      'reviews', 'website', 'whatsapp', 'telegram', 'vkontakte', 'instagram', 'url')
             if lead.get(k) not in (None, '')}
    wa = _wa_link(lead)
    if wa:
        facts['whatsapp_link'] = wa
    src = content.get('source') or {}
    parts = ['Ты веб-дизайнер. Сверстай современный одностраничный сайт для местного бизнеса — это демо, '
             'которое я отправлю владельцу, чтобы предложить сделать ему сайт.',
             '', 'ДАННЫЕ ИЗ 2ГИС:', json.dumps(facts, ensure_ascii=False, indent=1)]
    for s in src.get('sites') or []:
        if s.get('error'):
            continue
        parts += ['', 'С ИХ САЙТА/СОЦСЕТИ (%s):' % s.get('url')]
        if s.get('title') or s.get('description'):
            parts.append(' — '.join(x for x in (s.get('title'), s.get('description')) if x))
        if s.get('prices'):
            parts += ['Цены и услуги:'] + ['- ' + p for p in s['prices'][:30]]
        if s.get('text'):
            parts += ['Тексты:', '\n'.join(s['text'])[:3000]]
    gis = src.get('gis') or {}
    if gis.get('description'):
        parts += ['', 'ОПИСАНИЕ В 2ГИС:', gis['description']]
    if gis.get('reviews'):
        parts += ['', 'ОТЗЫВЫ КЛИЕНТОВ (можно процитировать 2–3, имена не выдумывай):'] + \
                 ['- ' + r[:400] for r in gis['reviews'][:5]]
    photos = [p for p in (content.get('photos') or []) if isinstance(p, str)][:8]
    if photos:
        parts += ['', 'ФОТО ЗАВЕДЕНИЯ (%d шт.) — используй все: первое на первом экране, остальные в галерее. '
                      'В src пиши ровно эти метки, программа подставит фото сама:' % len(photos)]
        for i, p in enumerate(photos):
            parts.append('- %s' % (PHOTO_TOKEN % (i + 1)))
    else:
        parts += ['', 'Фото нет: сделай красивый сайт без картинок (типографика, цвет, формы). '
                      'Не вставляй чужие фото и стоки.']
    parts += ['', 'ПОЖЕЛАНИЯ: ' + (wishes.strip() or 'на твой вкус, под эту сферу бизнеса')]
    parts += ['', 'ТРЕБОВАНИЯ:',
              '- один HTML-файл: весь CSS внутри <style>, без внешних библиотек и без JavaScript-фреймворков;',
              '- сначала мобильная версия, потом компьютер; русский язык;',
              '- только реальные данные: не выдумывай цены, адреса, телефоны, награды и имена;',
              '- кнопки записи/связи ведут в WhatsApp (whatsapp_link), на телефон (tel:) или в 2ГИС (url);',
              '- шрифты — системные или Google Fonts;',
              '- внизу мелко «Демо-версия сайта».',
              '', 'Ответь ТОЛЬКО кодом страницы в блоке ```html ... ```, без пояснений.']
    return '\n'.join(parts)


def parse_site_answer(text: str) -> str:
    """Достаёт HTML-страницу из ответа Claude."""
    t = text or ''
    m = re.search(r'```(?:html)?\s*(.*?)```', t, re.S | re.I)
    if m and '<' in m.group(1):
        t = m.group(1)
    start = re.search(r'<!doctype html|<html', t, re.I)
    if not start:
        raise ValueError('В ответе нет HTML-страницы. Скопируйте ответ Claude целиком (кнопка Copy под кодом).')
    t = t[start.start():]
    end = t.lower().rfind('</html>')
    if end == -1:
        raise ValueError('Страница обрезана — Claude не дописал её до конца. Напишите ему «продолжи» и вставьте целиком.')
    return t[:end + 7]


def fill_photos(page: str, photos: List[str]) -> str:
    for i, p in enumerate(photos[:8]):
        page = page.replace(PHOTO_TOKEN % (i + 1), html.escape(p, quote=True))
    # фото, которых нет, — прозрачная точка, чтобы не было «битых» картинок
    return re.sub(r'\{\{PHOTO_\d+\}\}', 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', page)

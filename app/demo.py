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
        'cta': 'Записаться', 'theme': 'warm'}),
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
    'warm':  {'title': 'Тёплый',  'bg': '#FBF7F2', 'card': '#FFFFFF', 'ink': '#2B211A', 'ink2': '#7A6B5E', 'accent': '#8A5A3B', 'accent_ink': '#FFFFFF', 'line': '#EADFD3'},
    'dark':  {'title': 'Тёмный',  'bg': '#0E0E10', 'card': '#18181B', 'ink': '#F4F4F5', 'ink2': '#A1A1AA', 'accent': '#F4F4F5', 'accent_ink': '#0E0E10', 'line': '#2A2A2F'},
    'fresh': {'title': 'Свежий',  'bg': '#F4F8F7', 'card': '#FFFFFF', 'ink': '#13231F', 'ink2': '#5C6F69', 'accent': '#0F7B63', 'accent_ink': '#FFFFFF', 'line': '#DCE8E4'},
}


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


def default_content(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Стартовые тексты демо: из данных 2ГИС и пресета сферы."""
    p = _preset(lead)
    cat = _first(lead.get('category')).lower()
    city = lead.get('city') or ''
    rating = _rating(lead)
    sub_parts = []
    if cat:
        sub_parts.append(cat.capitalize())
    if city:
        sub_parts.append(city)
    if rating:
        n = lead.get('reviews')
        sub_parts.append('рейтинг %s в 2ГИС' % rating + (' по %s отзывам' % n if n else ''))
    return {
        'headline': p['headline'],
        'subtitle': ' · '.join(sub_parts) or (lead.get('address') or ''),
        'about': ('%s — %s%s. Приходите — покажем, почему нас выбирают и возвращаются снова.'
                  % (quoted(lead.get('name') or 'Мы'), cat or 'место, где ценят ваше время',
                     ', ' + lead['address'] if lead.get('address') else '')),
        'services': list(p['services']),
        'cta': p['cta'],
        'theme': p['theme'],
        'photos': [],
    }


# ---------- промпт для Claude ----------

def claude_prompt(lead: Dict[str, Any], content: Dict[str, Any]) -> str:
    facts = {k: lead.get(k) for k in ('name', 'category', 'city', 'address', 'rating', 'reviews', 'schedule')
             if lead.get(k) not in (None, '')}
    return (
        'Ты копирайтер. Напиши тексты для одностраничного сайта местного бизнеса.\n'
        'Данные из 2ГИС:\n%s\n\n'
        'Сейчас на сайте черновик:\n%s\n\n'
        'Требования: по-русски, живо и конкретно, без канцелярита и восклицательных знаков, '
        'ничего не выдумывай про цены, адреса и награды. Заголовок до 60 символов, подзаголовок до 120, '
        '«о нас» 2–3 предложения, 6 услуг по 2–5 слов, текст кнопки 1–3 слова.\n\n'
        'Ответь ТОЛЬКО JSON без пояснений, ровно с такими ключами:\n'
        '{"headline": "...", "subtitle": "...", "about": "...", "services": ["...", "..."], "cta": "..."}'
        % (json.dumps(facts, ensure_ascii=False, indent=1),
           json.dumps({k: content.get(k) for k in ('headline', 'subtitle', 'about', 'services', 'cta')},
                      ensure_ascii=False, indent=1)))


def parse_claude_answer(text: str) -> Dict[str, Any]:
    """Достаёт JSON из ответа Claude (в том числе из ```json ... ```)."""
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        raise ValueError('В ответе нет JSON. Скопируйте ответ Claude целиком.')
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ValueError('Не получилось прочитать JSON из ответа Claude. Попросите его ответить только JSON.')
    out: Dict[str, Any] = {}
    for k in ('headline', 'subtitle', 'about', 'cta'):
        if isinstance(data.get(k), str) and data[k].strip():
            out[k] = data[k].strip()[:400]
    if isinstance(data.get('services'), list):
        out['services'] = [str(s).strip()[:80] for s in data['services'] if str(s).strip()][:9]
    if not out:
        raise ValueError('В JSON нет нужных полей (headline, subtitle, about, services, cta).')
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


def render(lead: Dict[str, Any], content: Dict[str, Any]) -> str:
    if content.get('custom_html'):  # «Свой дизайн» от Claude
        return fill_photos(content['custom_html'], [p for p in content.get('photos') or [] if isinstance(p, str)])
    e = lambda s: html.escape(str(s or ''), quote=True)  # noqa: E731
    t = THEMES.get(content.get('theme') or '', THEMES['fresh'])
    name = lead.get('name') or 'Демо'
    phone = _first(lead.get('phone'))
    tel = _tel(phone)
    wa = _wa_link(lead)
    rating = _rating(lead)
    links = []
    for key, title in (('whatsapp', 'WhatsApp'), ('telegram', 'Telegram'), ('vkontakte', 'ВКонтакте'),
                       ('instagram', 'Instagram'), ('viber', 'Viber')):
        u = _first(lead.get(key)) if key != 'whatsapp' else wa
        if u.startswith('http'):
            links.append('<a class="chip" href="%s" target="_blank" rel="noopener">%s</a>' % (e(u), title))
    map_url = _first(lead.get('url')) or _first(lead.get('yandex'))
    primary = wa or (('tel:' + tel) if tel else '') or (links and re.search(r'href="([^"]+)"', links[0]).group(1)) or '#contacts'
    photos = [p for p in (content.get('photos') or []) if isinstance(p, str)
              and (p.startswith('data:image/') or p.startswith('https://'))][:6]
    services = [s for s in (content.get('services') or []) if s][:9]

    gallery = ''
    if photos:
        gallery = '<section class="wrap"><div class="gallery">%s</div></section>' % ''.join(
            '<img src="%s" alt="%s" loading="lazy">' % (p, e(name)) for p in photos)
    hero_img = ('<div class="hero-img" style="background-image:url(%s)"></div>' % photos[0]) if photos else ''

    sched = lead.get('schedule') or ''
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<style>
:root{{--bg:{bg};--card:{card};--ink:{ink};--ink2:{ink2};--acc:{acc};--acc-ink:{acc_ink};--line:{line}}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}}
a{{color:inherit}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}
header{{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}}
header .wrap{{display:flex;align-items:center;justify-content:space-between;gap:12px;height:62px}}
.logo{{font-weight:700;font-size:17px;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:46px;padding:0 20px;border-radius:12px;background:var(--acc);color:var(--acc-ink);font-weight:600;text-decoration:none;white-space:nowrap;border:1px solid var(--acc)}}
.btn.ghost{{background:transparent;color:var(--ink);border-color:var(--line)}}
header .btn{{height:38px;padding:0 14px;font-size:14px}}
.hero{{display:grid;grid-template-columns:1.1fr .9fr;gap:36px;align-items:center;padding:56px 0 40px}}
.badge{{display:inline-flex;gap:8px;align-items:center;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:5px 12px;font-size:13.5px;color:var(--ink2)}}
.badge b{{color:var(--ink)}}
h1{{font-size:clamp(32px,5vw,52px);line-height:1.08;letter-spacing:-.025em;margin:16px 0 14px}}
.sub{{color:var(--ink2);font-size:18px;margin:0 0 26px;max-width:560px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap}}
.hero-img{{aspect-ratio:4/5;border-radius:22px;background:var(--card) center/cover;border:1px solid var(--line)}}
.hero-card{{border-radius:22px;background:var(--card);border:1px solid var(--line);padding:28px}}
.hero-card .big{{font-size:56px;font-weight:700;letter-spacing:-.03em;line-height:1}}
.hero-card p{{color:var(--ink2);margin:8px 0 0}}
section{{padding:28px 0}}
h2{{font-size:28px;letter-spacing:-.02em;margin:0 0 18px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.svc{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 18px;font-weight:600;display:flex;gap:12px;align-items:center}}
.svc i{{flex:none;width:28px;height:28px;border-radius:8px;background:color-mix(in srgb,var(--acc) 14%,transparent);color:var(--acc);display:grid;place-items:center;font-style:normal;font-size:13px}}
.about{{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px;font-size:17px;color:var(--ink2)}}
.gallery{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.gallery img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:14px;border:1px solid var(--line)}}
.contacts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}}
.box small{{display:block;color:var(--ink2);font-size:13px;margin-bottom:4px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.chip{{border:1px solid var(--line);border-radius:999px;padding:6px 12px;text-decoration:none;font-size:14px}}
.cta{{text-align:center;padding:48px 20px;border-radius:24px;background:var(--acc);color:var(--acc-ink);margin:20px 0 10px}}
.cta h2{{margin-bottom:8px}} .cta p{{opacity:.8;margin:0 0 20px}}
.cta .btn{{background:var(--acc-ink);color:var(--acc);border-color:var(--acc-ink)}}
footer{{color:var(--ink2);font-size:13px;text-align:center;padding:26px 0 90px}}
.float{{position:fixed;left:16px;right:16px;bottom:16px;display:none;z-index:6}}
.float .btn{{width:100%;box-shadow:0 10px 30px rgba(0,0,0,.25)}}
@media (max-width:820px){{.hero{{grid-template-columns:1fr;padding:32px 0 20px}}.hero-img{{aspect-ratio:16/10}}.grid,.gallery{{grid-template-columns:1fr 1fr}}.contacts{{grid-template-columns:1fr}}.float{{display:block}}header .btn{{display:none}}}}
</style></head>
<body>
<header><div class="wrap"><div class="logo">{name}</div>{head_btn}</div></header>
<main class="wrap">
  <section class="hero">
    <div>
      {badge}
      <h1>{headline}</h1>
      <p class="sub">{subtitle}</p>
      <div class="actions"><a class="btn" href="{primary}" target="_blank" rel="noopener">{cta}</a>{call_btn}</div>
    </div>
    {hero_right}
  </section>
  <section><h2>Услуги</h2><div class="grid">{services}</div></section>
  <section><h2>О нас</h2><div class="about">{about}</div></section>
</main>
{gallery}
<main class="wrap">
  <section id="contacts"><h2>Контакты</h2><div class="contacts">
    <div class="box"><small>Адрес</small>{address}{map}</div>
    <div class="box"><small>Часы работы</small>{schedule}</div>
    <div class="box"><small>Телефон</small>{phone_html}</div>
    <div class="box"><small>Мессенджеры и соцсети</small>{links}</div>
  </div></section>
  <div class="cta"><h2>{cta_title}</h2><p>Ответим и подберём удобное время</p><a class="btn" href="{primary}" target="_blank" rel="noopener">{cta}</a></div>
</main>
<footer>{name} · {city}<br>Демо-версия сайта</footer>
<div class="float"><a class="btn" href="{primary}" target="_blank" rel="noopener">{cta}</a></div>
</body></html>""".format(
        title=e(name + (' — ' + _first(lead.get('category')) if lead.get('category') else '')),
        desc=e(content.get('subtitle') or ''),
        bg=t['bg'], card=t['card'], ink=t['ink'], ink2=t['ink2'], acc=t['accent'], acc_ink=t['accent_ink'], line=t['line'],
        name=e(name),
        head_btn=('<a class="btn" href="tel:%s">%s</a>' % (e(tel), e(phone))) if tel else '',
        badge=('<span class="badge">★ <b>%s</b> рейтинг в 2ГИС%s</span>'
               % (rating, (' · %s отзывов' % e(lead.get('reviews'))) if lead.get('reviews') else '')) if rating else '',
        headline=e(content.get('headline')),
        subtitle=e(content.get('subtitle')),
        primary=e(primary), cta=e(content.get('cta') or 'Связаться'),
        call_btn=('<a class="btn ghost" href="tel:%s">Позвонить</a>' % e(tel)) if tel and primary.startswith('http') else '',
        hero_right=hero_img or ('<div class="hero-card"><div class="big">%s</div><p>%s</p></div>'
                                % ((('★ ' + rating) if rating else e(name[:1].upper())),
                                   ('средняя оценка гостей в 2ГИС' if rating else e(_first(lead.get('category')))))),
        services=''.join('<div class="svc"><i>%d</i>%s</div>' % (i + 1, e(s)) for i, s in enumerate(services)),
        about=e(content.get('about')),
        gallery=gallery,
        address=e(lead.get('address') or '—'),
        map=(' <div class="chips"><a class="chip" href="%s" target="_blank" rel="noopener">Открыть на карте</a></div>'
             % e(map_url)) if map_url else '',
        schedule=e(sched or 'Уточняйте по телефону'),
        phone_html=('<a href="tel:%s">%s</a>' % (e(tel), e(phone))) if tel else '—',
        links=('<div class="chips">%s</div>' % ''.join(links)) if links else '—',
        cta_title=e(content.get('cta') or 'Свяжитесь с нами'),
        city=e(lead.get('city') or ''),
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

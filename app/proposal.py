"""A saved proposal and an offline, shareable first-screen prototype."""
from __future__ import annotations

from html import escape

PRESETS = {
    'quote': ('Узнать предварительную стоимость', 'Отправить на расчёт', (
        ('Что нужно рассчитать?', 'Опишите задачу', 'text'),
        ('Как с вами связаться?', 'Телефон или Telegram', 'text'),
    )),
    'booking': ('Записаться на услугу', 'Запросить запись', (
        ('Услуга', 'Какую услугу выбрать?', 'text'),
        ('Удобная дата', '', 'date'),
        ('Как с вами связаться?', 'Телефон или Telegram', 'text'),
    )),
    'request': ('Оставить заявку', 'Отправить заявку', (
        ('С чем помочь?', 'Опишите свой запрос', 'text'),
        ('Как с вами связаться?', 'Телефон или Telegram', 'text'),
    )),
}


def normalize(fields: dict) -> dict:
    preset = fields.get('preset')
    clean = {'preset': preset if isinstance(preset, str) and preset in PRESETS else 'request'}
    for key, limit in (('topic', 160), ('offer', 1200), ('message', 1600), ('headline', 90), ('intro', 220)):
        value = fields.get(key)
        clean[key] = value.strip()[:limit] if isinstance(value, str) else ''
    return clean


def starter(lead: dict, report: dict) -> dict:
    name = str(lead.get('name') or 'вашей компании')
    offer = report.get('offer') or ''
    index = report.get('offer_topic')
    topics = report.get('topics') or []
    topic = topics[index]['title'] if type(index) is int and 0 <= index < len(topics) else ''
    lower = (topic + ' ' + offer + ' ' + str(lead.get('category') or '')).lower()
    preset = ('booking' if any(token in lower for token in ('запис', 'расписан'))
              else 'quote' if any(token in lower for token in ('цен', 'расчёт', 'расчет', 'стоимост', 'детейл'))
              else 'request')
    message = report.get('message') or (
        f'Здравствуйте! Посмотрел, как клиенты обращаются в «{name}». '
        'Могу показать короткое демо, как упростить этот процесс. Вам интересно посмотреть?'
    )
    return normalize({'topic': topic, 'offer': offer, 'message': message, 'preset': preset})


def render(lead: dict, fields: dict) -> str:
    data = normalize(fields)
    name = escape(str(lead.get('name') or 'Ваша компания'))
    location = escape(' · '.join(str(lead[k]) for k in ('category', 'city') if lead.get(k)))
    title, action, inputs = PRESETS[data['preset']]
    title = escape(data['headline'] or title)
    intro = escape(data['intro'] or 'Оставьте детали, чтобы с вами могли связаться и уточнить запрос.')
    controls = ''.join(
        f'<label>{label}<input type="{kind}" placeholder="{placeholder}" required></label>'
        for label, placeholder, kind in inputs
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — демо</title>
<style>
:root{{color-scheme:dark;--paper:#eae5dc;--ink:#17191b;--muted:#aba8a2;--panel:#24272a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--ink);color:var(--paper);
font:16px/1.5 system-ui,sans-serif}}main{{max-width:1000px;margin:auto;padding:clamp(22px,6vw,72px)}}
header{{display:flex;justify-content:space-between;gap:24px;border-bottom:1px solid #55504b;padding-bottom:16px}}
header strong{{font-size:1.1rem;overflow-wrap:anywhere}}header small,p{{color:var(--muted)}}
.hero{{display:grid;grid-template-columns:1fr minmax(250px,.8fr);gap:clamp(26px,6vw,64px);
align-items:start;padding-top:52px}}h1{{line-height:1.06;margin:.35em 0;font-size:clamp(32px,6vw,70px);
letter-spacing:-.045em;overflow-wrap:anywhere}}section>p{{max-width:32ch}}
form{{display:grid;gap:15px;padding:24px;background:var(--panel);border-radius:16px}}label{{display:grid;gap:6px}}
input,button{{width:100%;font:inherit;padding:11px 13px;border:1px solid #626264;border-radius:7px}}
input{{background:#1b1e20;color:inherit}}button{{background:var(--paper);color:var(--ink);font-weight:700;
cursor:pointer}}:focus-visible{{outline:2px solid #ecd194;outline-offset:3px}}[role=status]{{margin:0}}
@media(max-width:660px){{.hero{{grid-template-columns:1fr;padding-top:24px}}}}
</style></head>
<body><main><header><strong>{name}</strong><small>Демо</small></header>
<div class="hero"><section><p>{location}</p><h1>{title}</h1>
<p>{intro}</p></section>
<form autocomplete="off">{controls}
<button type="submit">{action}</button><small>Пробная форма. Данные никуда не отправляются.</small>
<p role="status" hidden>Так будет выглядеть подтверждение. Это демо, заявка не отправлена.</p>
</form></div></main>
<script>
const form = document.querySelector('form');
form.addEventListener('submit', event => {{
  event.preventDefault();
  form.reset();
  form.querySelector('[role="status"]').hidden = false;
}});
</script></body></html>"""

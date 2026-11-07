"""Самопроверка собранного приложения: `Parser --selftest`.

Проверяет, что внутрь сборки попали ядро, справочники и Excel-экспорт,
и что на машине находится браузер. Код возврата 0 = всё хорошо.
Отчёт пишется в selftest.txt рядом с настройками (в оконной сборке нет консоли).
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

SAMPLE_DOC = {
    'meta': {'code': 200},
    'result': {'items': [{
        'id': '70000001012345678_abc',
        'locale': 'ru_RU',
        'type': 'branch',
        'name_ex': {'primary': 'Кофейня «Тест»', 'extension': 'кофейня'},
        'address_name': 'Ленина проспект, 1',
        'address_comment': '1 этаж',
        'adm_div': [{'type': 'city', 'name': 'Екатеринбург'},
                    {'type': 'district', 'name': 'Ленинский район'}],
        'org': {'id': '7000000', 'name': 'Тест', 'branch_count': 3},
        'point': {'lat': 56.838, 'lon': 60.597},
        'reviews': {'general_rating': 4.7, 'general_review_count': 128},
        'rubrics': [{'id': '1', 'kind': 'primary', 'name': 'Кофейни', 'short_id': 1}],
        'schedule': {d: {'working_hours': [{'from': '08:00', 'to': '22:00'}]}
                     for d in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')},
        'contact_groups': [{'contacts': [
            {'type': 'phone', 'value': '+73433001234', 'text': '+7 (343) 300‒12‒34'},
            {'type': 'phone', 'value': '+79120001122', 'text': '+7 912 000‒11‒22'},
            {'type': 'email', 'value': 'hello@test-coffee.ru'},
            {'type': 'website', 'value': 'http://test-coffee.ru',
             'url': 'https://link.2gis.ru/1.2/AAAA?http://test-coffee.ru'},
            {'type': 'whatsapp', 'value': 'https://wa.me/79120001122',
             'url': 'https://wa.me/79120001122?text=hi'},
            {'type': 'telegram', 'value': 'https://t.me/testcoffee', 'url': 'https://t.me/testcoffee'},
            {'type': 'vkontakte', 'value': 'vk.com/testcoffee', 'url': 'https://vk.com/testcoffee'},
        ]}],
    }]},
}


def run() -> int:
    lines = []
    ok = True
    try:
        from parser_2gis.paths import data_path
        from .browsers import find_browsers
        from .excel import write_xlsx
        from .records import FIELDS, parse_doc
        from .api import Api, _load_cities, _load_localities

        lines.append('data_path: %s' % data_path())
        cities = _load_cities()
        lines.append('cities: %d' % len(cities))
        locs = _load_localities()
        assert len(locs) > 500, 'localities.json не попал в сборку'
        polevskoy = Api()._resolve_city('loc:Полевской@ekaterinburg')
        assert polevskoy and polevskoy['alts'][0] == 'ekaterinburg', polevskoy
        lines.append('localities: %d, Полевской -> %s' % (len(locs), polevskoy['alts']))
        rec = parse_doc(SAMPLE_DOC)
        assert rec and rec['phone'].startswith('+7 (343)'), rec
        assert rec['website'] == 'http://test-coffee.ru', rec['website']
        from .records import site_kind
        assert rec['site_kind'] == 'Есть сайт', rec['site_kind']
        assert site_kind({'website': 'https://x.taplink.ws'}) == 'Шаблон'
        assert site_kind({'website': 'https://vk.com/x'}) == 'Только соцсети'
        assert site_kind({'phone': '+7'}) == 'Без сайта'
        lines.append('record: %s' % {k: rec[k] for k in ('name', 'phone', 'website', 'schedule')})
        out = os.path.join(tempfile.mkdtemp(), 'selftest.xlsx')
        write_xlsx(out, [rec], list(FIELDS))
        lines.append('xlsx: %d bytes' % os.path.getsize(out))
        from pathlib import Path
        from .base import LeadBase
        base = LeadBase(Path(tempfile.mkdtemp()) / 'leads.db')
        lead = base.add(dict(rec, _id='selftest'))
        base.set_status(lead['id'], 'replied')
        base.set_note(lead['id'], 'тест')
        assert base.counts() == {'replied': 1, 'all': 1}, base.counts()
        assert base.list('', 'тест')[0]['note'] == 'тест'
        base.close()
        lines.append('base: ok')
        from . import demo as demo_site
        from .base import render_message
        content = demo_site.default_content(rec)
        page = demo_site.render(rec, content)
        assert '<html' in page and rec['name'].split('«')[0].strip() in page
        assert demo_site.slugify('Кофейня «Тест»') == 'kofeynya-test', demo_site.slugify('Кофейня «Тест»')
        assert 'https://x' in render_message('{демо}', {'demo_url': 'https://x'})
        lines.append('demo: %d bytes' % len(page))
        import webview  # noqa: F401  — проверяем, что GUI-библиотека в сборке
        lines.append('webview: ok')
        browsers = find_browsers()
        lines.append('browsers: %s' % (browsers or 'НЕ НАЙДЕНО'))
        if '--live' in sys.argv:  # живой прогон: 3 аптеки в Москве и 3 в Полевском (город внутри региона)
            import time
            from .job import Job, Params, Task
            for label, city in (('Москва', {'name': 'Москва', 'code': 'moscow', 'domain': 'ru'}),
                                ('Полевской', Api()._resolve_city('loc:Полевской@ekaterinburg'))):
                job = Job()
                job.start(Params(tasks=[Task(city, 'аптека')], fields=list(FIELDS), limit=3,
                                 output_dir=tempfile.mkdtemp()))
                t0 = time.time()
                while job.running and time.time() - t0 < 240:
                    time.sleep(0.5)
                lines.append('live %s: state=%s rows=%d error=%s' % (label, job.state, len(job.rows), job.error))
                lines += ['  %s %s' % (l['level'], l['text']) for l in job.logs]
                ok = ok and len(job.rows) > 0
    except Exception:
        ok = False
        lines.append(traceback.format_exc())

    report = '\n'.join(lines + ['RESULT: %s' % ('OK' if ok else 'FAIL')])
    print(report)
    try:
        from .storage import data_dir
        with open(data_dir() / 'selftest.txt', 'w', encoding='utf-8') as f:
            f.write(report)
    except Exception:
        pass
    return 0 if ok else 1

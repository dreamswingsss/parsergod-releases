"""Мост между окном (JS) и Python. Каждый публичный метод доступен в JS как
window.pywebview.api.<имя>(...) и возвращает Promise."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from parser_2gis.paths import data_path

from . import brand
from . import demo as demo_site
from .base import STATUSES, TEMPLATE_VARS, LeadBase, lead_id, render_message
from .browsers import find_browsers
from .excel import write_xlsx
from .job import Job, Params, Task
from .records import FIELDS
from .storage import data_dir, default_output_dir, load_settings, safe_filename, save_settings

COUNTRY_NAMES = {
    'ru': 'Россия', 'kz': 'Казахстан', 'by': 'Беларусь', 'az': 'Азербайджан',
    'kg': 'Киргизия', 'uz': 'Узбекистан', 'cz': 'Чехия', 'eg': 'Египет',
    'it': 'Италия', 'sa': 'Саудовская Аравия', 'cy': 'Кипр', 'ae': 'ОАЭ',
    'cl': 'Чили', 'qa': 'Катар', 'om': 'Оман', 'bh': 'Бахрейн',
    'kw': 'Кувейт', 'iq': 'Ирак',
}

DEFAULT_FIELDS = ['name', 'category', 'site_kind', 'address', 'phone', 'whatsapp', 'telegram',
                  'email', 'website', 'vkontakte', 'instagram', 'rating', 'reviews', 'url', 'yandex']


def _load_cities() -> List[Dict[str, str]]:
    with open(data_path() / 'cities.json', 'r', encoding='utf-8') as f:
        cities = json.load(f)
    out = []
    for c in cities:
        out.append({'name': c['name'], 'code': c['code'], 'domain': c['domain'],
                    'country': COUNTRY_NAMES.get(c['country_code'], c['country_code'].upper()),
                    'cc': c['country_code']})
    out.sort(key=lambda c: (c['cc'] != 'ru', c['country'], c['name']))
    return out


def _norm(s: str) -> str:
    return (s or '').lower().replace('ё', 'е').strip()


def _localities_path() -> str:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return os.path.join(base, 'app', 'ui', 'localities.json')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui', 'localities.json')


def _load_localities() -> List[Dict[str, Any]]:
    """Города РФ, которых нет среди регионов 2ГИС: [{n: 'Полевской', r: 'Свердловская обл',
    c: ['ekaterinburg', ...]}] — c = регионы 2ГИС, в которых его искать (по порядку)."""
    try:
        with open(_localities_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _reveal(path: str) -> None:
    if sys.platform.startswith('win'):
        if os.path.isfile(path):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
        else:
            os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path] if os.path.isfile(path) else ['open', path])
    else:
        subprocess.Popen(['xdg-open', os.path.dirname(path) if os.path.isfile(path) else path])


def _dialog(kind: str):
    """Константа типа диалога для pywebview 5 и 6."""
    import webview
    fd = getattr(webview, 'FileDialog', None)
    if fd is not None:
        return getattr(fd, kind)
    return getattr(webview, kind + '_DIALOG')


def _open(path: str) -> None:
    if sys.platform.startswith('win'):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


class Api:
    def __init__(self) -> None:
        self._window = None
        self._job = Job()
        self._cities = _load_cities()
        self._by_code = {c['code']: c for c in self._cities}
        self._localities = [x for x in _load_localities() if 'c' in x]  # c=[] — города нет в 2ГИС, окно так и скажет
        self._loc_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for x in self._localities:
            x['c'] = [c for c in x['c'] if c in self._by_code]
            self._loc_by_name.setdefault(_norm(x['n']), []).append(x)
        self._settings = load_settings()
        self._base = LeadBase(data_dir() / 'leads.db')

    def _resolve_city(self, key: str) -> Optional[Dict[str, str]]:
        """'ekaterinburg' -> регион 2ГИС; 'loc:Полевской@ekaterinburg' -> населённый пункт внутри региона.

        Для населённого пункта собираем запасные регионы (alts): если в первом его не окажется,
        сбор сам попробует следующий."""
        if key.startswith('loc:') and '@' in key:
            name, code = key[4:].rsplit('@', 1)
            region = self._by_code.get(code)
            name = name.strip()
            if not region or not name:
                return None
            alts = [code]
            known = self._loc_by_name.get(_norm(name), [])
            for loc in sorted(known, key=lambda x: code not in x['c']):
                alts += [c for c in loc['c'] if c not in alts]
                break  # одноимённые города в разных областях не смешиваем
            names = {c: self._by_code[c]['name'] for c in alts}
            return dict(region, name=name, locality=name, region=region['name'], key=key,
                        alts=alts, alt_names=names)
        region = self._by_code.get(key)
        return dict(region, key=key) if region else None

    @staticmethod
    def _fields_with_new(s: Dict[str, Any]) -> List[str]:
        """Сохранённые поля + колонка «Тип сайта» один раз после обновления до 1.1."""
        fields = list(s.get('fields', DEFAULT_FIELDS))
        if not s.get('seen_site_kind'):
            if 'site_kind' not in fields:
                fields.append('site_kind')
            s['seen_site_kind'] = True
            s['fields'] = fields
            save_settings(s)
        return fields

    def attach(self, window) -> None:
        self._window = window

    # ---------- данные для окна ----------
    def init(self) -> Dict[str, Any]:
        s = self._settings
        return {
            'app': {'name': brand.APP_NAME, 'title': brand.APP_TITLE,
                    'version': brand.APP_VERSION, 'support': brand.SUPPORT_CONTACT},
            'cities': self._cities,
            'localities': self._localities,
            'fields': [{'key': k, 'title': v['title'], 'group': v['group']} for k, v in FIELDS.items()],
            'browsers': [b[0] for b in find_browsers()],
            'settings': {
                'cities': [],  # каждый запуск — с чистого листа
                'queries': [],
                'fields': self._fields_with_new(s),
                'limit': s.get('limit', 100),
                'require_any': s.get('require_any', False),
                'require_phone': s.get('require_phone', True),
                'one_per_chain': s.get('one_per_chain', False),
                'min_rating': s.get('min_rating', 0),
                'no_site': s.get('no_site', False),
                'view': s.get('view', 'cards'),
                'show_browser': s.get('show_browser', False),
                'delay_ms': s.get('delay_ms', 0),
                'browser_path': s.get('browser_path', ''),
                'output_dir': s.get('output_dir') or str(default_output_dir()),
                'source': '2gis',
            },
            'recent': [],  # история сборов на старте не показывается
            'statuses': STATUSES,
            'base_ids': self._base.ids(),
            'demo_urls': self._base.demo_urls(),
            'templates': self._base.templates(),
            'template_vars': TEMPLATE_VARS,
            'active_template': s.get('active_template', ''),
            'vercel': {'connected': bool(s.get('vercel_token')), 'account': s.get('vercel_account', '')},
            'themes': {k: v['title'] for k, v in demo_site.THEMES.items()},
        }

    def recent(self) -> List[Dict[str, Any]]:
        out_dir = self._settings.get('output_dir') or str(default_output_dir())
        try:
            files = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                     if f.lower().endswith('.xlsx') and not f.startswith('~$')]
        except OSError:
            return []
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return [{'path': p, 'name': os.path.basename(p)[:-5],
                 'when': time.strftime('%d.%m %H:%M', time.localtime(os.path.getmtime(p)))}
                for p in files[:6]]

    # ---------- сбор ----------
    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cities = [c for c in (self._resolve_city(k) for k in payload.get('cities', [])) if c]
            queries = [q.strip() for q in payload.get('queries', []) if q and q.strip()]
            fields = [f for f in payload.get('fields', []) if f in FIELDS]
            if not cities:
                return {'ok': False, 'error': 'Выберите город'}
            if not queries:
                return {'ok': False, 'error': 'Введите, что искать (например: кофейня)'}
            if not fields:
                return {'ok': False, 'error': 'Отметьте хотя бы одно поле для таблицы'}

            limit = payload.get('limit')
            limit = int(limit) if limit else None
            contact_fields = [f for f in fields if FIELDS[f]['group'] == 'contact']
            output_dir = payload.get('output_dir') or str(default_output_dir())

            params = Params(
                tasks=[Task(city=c, query=q) for c in cities for q in queries],
                fields=fields,
                limit=limit,
                require_any=contact_fields if payload.get('require_any') else [],
                require_phone=bool(payload.get('require_phone')),
                one_per_chain=bool(payload.get('one_per_chain')),
                min_rating=float(payload.get('min_rating') or 0),
                no_site=bool(payload.get('no_site')),
                show_browser=bool(payload.get('show_browser')),
                delay_ms=int(payload.get('delay_ms') or 0),
                browser_path=(payload.get('browser_path') or '').strip() or None,
                output_dir=output_dir,
                source='2gis',  # Яндекс Карты временно отключены (код в app/yandex.py)
            )
            self._settings.update({
                'cities': [c['key'] for c in cities], 'queries': queries, 'fields': fields,
                'limit': limit, 'require_any': bool(payload.get('require_any')),
                'require_phone': params.require_phone, 'one_per_chain': params.one_per_chain,
                'min_rating': params.min_rating, 'no_site': params.no_site, 'show_browser': params.show_browser,
                'delay_ms': params.delay_ms, 'browser_path': params.browser_path or '',
                'output_dir': output_dir, 'source': params.source,
            })
            save_settings(self._settings)
            self._job.start(params)
            return {'ok': True, 'tasks': len(params.tasks)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ---------- база лидов ----------
    def base_add(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        if not rec or not rec.get('name'):
            return {'ok': False, 'error': 'Пустая карточка'}
        if rec.get('_lid') and not rec.get('_id'):
            rec = dict(rec, _id=rec['_lid'])
        return {'ok': True, 'lead': self._base.add(rec), 'counts': self._base.counts()}

    def base_remove(self, lid: str) -> Dict[str, Any]:
        self._base.remove(lid)
        return {'ok': True, 'counts': self._base.counts()}

    def base_list(self, status: str = '', search: str = '') -> Dict[str, Any]:
        return {'items': self._base.list(status, search), 'counts': self._base.counts()}

    def base_status(self, lid: str, status: str) -> Dict[str, Any]:
        try:
            self._base.set_status(lid, status)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        return {'ok': True, 'counts': self._base.counts()}

    def base_note(self, lid: str, note: str) -> Dict[str, Any]:
        self._base.set_note(lid, note)
        return {'ok': True}

    def base_export(self, status: str = '', search: str = '', fields: Optional[List[str]] = None) -> Dict[str, Any]:
        rows = self._base.list(status, search)
        if not rows:
            return {'ok': False, 'error': 'В базе нет лидов под этот фильтр'}
        for r in rows:
            r['_status'] = r['status_title']
            r['_added'] = r['added']
        cols = [f for f in (fields or self._settings.get('fields') or DEFAULT_FIELDS) if f in FIELDS]
        out_dir = self._settings.get('output_dir') or str(default_output_dir())
        os.makedirs(out_dir, exist_ok=True)
        label = ('База — ' + STATUSES[status]) if status in STATUSES else 'База лидов'
        path = os.path.join(out_dir, safe_filename('%s — %s' % (label, time.strftime('%Y-%m-%d %H-%M'))) + '.xlsx')
        try:
            write_xlsx(path, rows, cols, extra=[('_status', 'Статус'), ('note', 'Заметка'), ('_added', 'Добавлен')])
        except Exception as e:
            return {'ok': False, 'error': str(e)}
        return {'ok': True, 'path': path, 'n': len(rows)}

    # ---------- Vercel ----------
    def vercel_connect(self, token: str) -> Dict[str, Any]:
        token = (token or '').strip()
        if not token:
            return {'ok': False, 'error': 'Вставьте токен'}
        try:
            account = demo_site.vercel_check(token)
        except demo_site.VercelError as e:
            return {'ok': False, 'error': str(e)}
        self._settings.update({'vercel_token': token, 'vercel_account': account})
        save_settings(self._settings)
        return {'ok': True, 'account': account}

    def vercel_disconnect(self) -> Dict[str, Any]:
        self._settings.pop('vercel_token', None)
        self._settings.pop('vercel_account', None)
        save_settings(self._settings)
        return {'ok': True}

    # ---------- демо-сайт ----------
    def _lead_for(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Лид из базы; если его там нет — добавляем (демо и переписка живут в базе)."""
        if rec.get('id') and self._base.get(rec['id']):
            return self._base.get(rec['id']) or {}
        if rec.get('_lid') and not rec.get('_id'):
            rec = dict(rec, _id=rec['_lid'])
        return self._base.add(rec)

    def demo_open(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        lead = self._lead_for(rec)
        if not lead:
            return {'ok': False, 'error': 'Пустая карточка'}
        content = self._base.get_demo(lead['id']) or demo_site.default_content(lead)
        return {'ok': True, 'lead': lead, 'content': content, 'html': demo_site.render(lead, content),
                'counts': self._base.counts()}

    def demo_preview(self, lid: str, content: Dict[str, Any]) -> str:
        lead = self._base.get(lid) or {}
        return demo_site.render(lead, content or {})

    def demo_reset(self, lid: str) -> Dict[str, Any]:
        lead = self._base.get(lid) or {}
        content = demo_site.default_content(lead)
        return {'content': content, 'html': demo_site.render(lead, content)}

    def demo_prompt(self, lid: str, content: Dict[str, Any]) -> str:
        return demo_site.claude_prompt(self._base.get(lid) or {}, content or {})

    def demo_apply(self, text: str) -> Dict[str, Any]:
        try:
            return {'ok': True, 'fields': demo_site.parse_claude_answer(text)}
        except ValueError as e:
            return {'ok': False, 'error': str(e)}

    def demo_enrich(self, lid: str, content: Dict[str, Any]) -> Dict[str, Any]:
        """Подтягивает данные с сайта/VK заведения и со страницы 2ГИС."""
        from . import enrich
        lead = self._base.get(lid)
        if not lead:
            return {'ok': False, 'error': 'Лид не найден'}
        try:
            got = enrich.gather(lead)
        except Exception as e:
            return {'ok': False, 'error': 'Не получилось собрать данные: %s' % e}
        content = dict(content or {})
        content['source'] = {'sites': got['sites'], 'gis': got['gis']}
        have = list(content.get('photos') or [])
        for u in got['photos']:
            if u not in have and len(have) < 8:
                have.append(u)
        content['photos'] = have
        self._base.save_demo(lid, content)
        return {'ok': True, 'content': content, 'summary': got['summary'],
                'html': demo_site.render(lead, content)}

    def demo_site_prompt(self, lid: str, content: Dict[str, Any], wishes: str = '') -> str:
        return demo_site.site_prompt(self._base.get(lid) or {}, content or {}, wishes or '')

    def demo_apply_site(self, lid: str, content: Dict[str, Any], text: str) -> Dict[str, Any]:
        """Ответ Claude (HTML) → «Свой дизайн» этого демо."""
        try:
            page = demo_site.parse_site_answer(text)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        content = dict(content or {}, custom_html=page)
        self._base.save_demo(lid, content)
        return {'ok': True, 'content': content, 'html': demo_site.render(self._base.get(lid) or {}, content)}

    def _demo_file(self, lead: Dict[str, Any], page: str) -> str:
        out_dir = os.path.join(self._settings.get('output_dir') or str(default_output_dir()), 'Демо')
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, safe_filename('Демо — %s' % lead.get('name')) + '.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(page)
        return path

    def demo_publish(self, lid: str, content: Dict[str, Any]) -> Dict[str, Any]:
        """Сохраняет демо файлом и, если подключён Vercel, публикует его на аккаунт пользователя."""
        lead = self._base.get(lid)
        if not lead:
            return {'ok': False, 'error': 'Лид не найден в базе'}
        content = dict(content or {})
        page = demo_site.render(lead, content)
        path = self._demo_file(lead, page)
        token = self._settings.get('vercel_token')
        if not token:
            self._base.save_demo(lid, content)
            return {'ok': True, 'path': path, 'url': ''}
        project = content.get('project') or '%s-%s' % (demo_site.slugify(lead.get('name') or '', 36), lid[-4:].lower())
        project = re.sub(r'[^a-z0-9-]', '', project.lower()).strip('-')[:90] or 'demo'
        content['project'] = project
        try:
            url = demo_site.vercel_publish(token, project, page)
        except demo_site.VercelError as e:
            self._base.save_demo(lid, content)
            return {'ok': False, 'error': str(e), 'path': path}
        self._base.save_demo(lid, content, url)
        return {'ok': True, 'path': path, 'url': url}

    def demo_save_file(self, lid: str, content: Dict[str, Any]) -> Dict[str, Any]:
        lead = self._base.get(lid)
        if not lead:
            return {'ok': False, 'error': 'Лид не найден'}
        self._base.save_demo(lid, content or {})
        path = self._demo_file(lead, demo_site.render(lead, content or {}))
        return {'ok': True, 'path': path}

    # ---------- сообщения ----------
    def templates(self) -> List[Dict[str, Any]]:
        return self._base.templates()

    def template_save(self, t: Dict[str, Any]) -> Dict[str, Any]:
        return {'ok': True, 'template': self._base.save_template(t.get('id'), t.get('title', ''), t.get('body', '')),
                'templates': self._base.templates()}

    def template_delete(self, tid: str) -> Dict[str, Any]:
        self._base.delete_template(tid)
        if self._settings.get('active_template') == tid:
            self._settings['active_template'] = ''
            save_settings(self._settings)
        return {'ok': True, 'templates': self._base.templates()}

    def set_active_template(self, tid: str) -> Dict[str, Any]:
        self._settings['active_template'] = tid or ''
        save_settings(self._settings)
        return {'ok': True}

    def preview_message(self, body: str) -> str:
        return render_message(body, {'name': 'Sweet Skin Studio', 'category': 'Косметология', 'city': 'Екатеринбург',
                                     'address': 'ул. Малышева, 51', 'rating': 4.8, 'reviews': 37,
                                     'demo_url': 'https://sweet-skin-studio-1a2b.vercel.app'})

    def message_prompt(self) -> str:
        """Промпт для Claude: варианты первого сообщения с переменными программы."""
        return ('Напиши 3 варианта первого сообщения владельцу местного бизнеса (салон, кафе, автосервис и т.п.), '
                'которому я предлагаю сделать сайт. Я уже сделал для него демо-сайт. Сообщение уходит в WhatsApp '
                'или Telegram, поэтому коротко: 2–4 предложения, на «вы», без восклицательных знаков, без '
                'слов «уникальный», «эффективный», «под ключ». Тон — как пишет живой человек, а не агентство.\n\n'
                'Используй переменные в фигурных скобках, программа подставит их сама: {название}, {категория}, '
                '{рейтинг}, {город}, {демо} — ссылка на демо-сайт (обязательно в каждом варианте).\n'
                'Варианты: 1) про то, что у них нет сайта; 2) про то, что клиенты пишут им только в соцсети; '
                '3) самый короткий.')

    def open_chat(self, rec: Dict[str, Any], channel: str, tid: str = '', force: bool = False) -> Dict[str, Any]:
        """Открывает чат с лидом. В WhatsApp текст подставляется сам, для остальных окно кладёт его
        в буфер обмена. Лид уходит в базу, статус «Новый» меняется на «Написал»."""
        lead = self._lead_for(rec)
        if not lead:
            return {'ok': False, 'error': 'Пустая карточка'}
        t = next((x for x in self._base.templates() if x['id'] == tid), None) if tid else None
        text = render_message(t['body'], lead) if t else ''
        need_demo = bool(t and '{демо}' in t['body'] and not lead.get('demo_url'))
        if need_demo and not force:  # окно спросит: сделать демо или отправить без ссылки
            return {'ok': False, 'need_demo': True, 'lid': lead['id'], 'counts': self._base.counts()}
        first = (lead.get(channel) or '').split(', ')[0].strip()
        if channel == 'whatsapp':
            m = re.search(r'(?:wa\.me/|phone=)\+?(\d{10,15})', first)
            num = m.group(1) if m else None
            if not num:
                for p in (lead.get('phone') or '').split(','):
                    d = re.sub(r'\D', '', p)
                    if len(d) == 11 and d[0] in '78' and d[1] in '97':
                        num = '7' + d[1:]
                        break
            if not num and not first:
                return {'ok': False, 'error': 'У этой организации нет WhatsApp'}
            url = ('https://wa.me/%s' % num if num else first) + (('?text=' + urllib.parse.quote(text)) if text and num else '')
        else:
            url = first
        if not url.startswith(('http://', 'https://')):
            return {'ok': False, 'error': 'У этой организации нет такого контакта'}
        self.open_url(url)
        marked = bool(text) and lead.get('status') == 'new'
        if marked:
            self._base.set_status(lead['id'], 'contacted')
        return {'ok': True, 'text': text, 'prefilled': channel == 'whatsapp' and bool(text) and '?text=' in url,
                'marked': marked, 'need_demo': need_demo, 'lid': lead['id'], 'counts': self._base.counts()}

    def set_view(self, view: str) -> Dict[str, Any]:
        """Карточки или таблица — запоминаем между запусками."""
        self._settings['view'] = 'table' if view == 'table' else 'cards'
        save_settings(self._settings)
        return {'ok': True}

    def stop(self) -> Dict[str, Any]:
        self._job.stop()
        return {'ok': True}

    def status(self, row_cursor: int = 0, log_cursor: int = 0) -> Dict[str, Any]:
        j = self._job
        if j.needs_visible and not self._settings.get('show_browser'):
            self._settings['show_browser'] = True  # в следующий раз сразу видимый браузер
            save_settings(self._settings)
        rows = j.rows[row_cursor:row_cursor + 200]
        logs = j.logs[log_cursor:]
        end = j.finished_at or time.time()
        return {
            'state': j.state,
            'count': len(j.rows),
            'rows': [dict({k: r.get(k) for k in list(FIELDS) + ['_query', '_photos']}, _lid=lead_id(r)) for r in rows],
            'row_cursor': row_cursor + len(rows),
            'logs': logs,
            'log_cursor': log_cursor + len(logs),
            'task_index': j.task_index,
            'task_count': j.task_count,
            'task_label': j.task_label,
            'elapsed': int(end - j.started_at) if j.started_at else 0,
            'file_path': j.file_path,
            'error': j.error,
            'attention': j.attention,
            'needs_visible': j.needs_visible,
        }

    # ---------- файлы ----------
    def open_file(self, path: str) -> Dict[str, Any]:
        try:
            if not os.path.exists(path) and not os.path.splitext(path)[1]:
                os.makedirs(path, exist_ok=True)  # папка выгрузок ещё не создана
            _open(path)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def reveal(self, path: str) -> Dict[str, Any]:
        try:
            _reveal(path)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def open_url(self, url: str) -> Dict[str, Any]:
        if not url.startswith(('https://', 'http://')):
            return {'ok': False}
        import webbrowser
        webbrowser.open(url)
        return {'ok': True}

    def save_as(self) -> Dict[str, Any]:
        if not self._job.rows or not self._window:
            return {'ok': False, 'error': 'Нет данных'}
        name = os.path.basename(self._job.file_path or 'Выгрузка.xlsx')
        res = self._window.create_file_dialog(_dialog('SAVE'), save_filename=name,
                                              file_types=('Excel (*.xlsx)',))
        if not res:
            return {'ok': False}
        path = res if isinstance(res, str) else res[0]
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        try:
            return {'ok': True, 'path': self._job.save_as(path)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def choose_folder(self) -> Optional[str]:
        if not self._window:
            return None
        res = self._window.create_file_dialog(_dialog('FOLDER'))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        self._settings['output_dir'] = path
        save_settings(self._settings)
        return path

    def choose_browser(self) -> Optional[str]:
        if not self._window:
            return None
        res = self._window.create_file_dialog(_dialog('OPEN'))
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    # ---------- закрытие ----------
    def shutdown(self) -> None:
        if self._job.running:
            self._job.stop()
            self._job.wait(8)
        try:
            self._base.close()
        except Exception:
            pass

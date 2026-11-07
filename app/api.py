"""Мост между окном (JS) и Python. Каждый публичный метод доступен в JS как
window.pywebview.api.<имя>(...) и возвращает Promise."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from parser_2gis.paths import data_path

from . import brand
from .base import STATUSES, LeadBase, lead_id
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
            'rows': [dict({k: r.get(k) for k in list(FIELDS) + ['_query']}, _lid=lead_id(r)) for r in rows],
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

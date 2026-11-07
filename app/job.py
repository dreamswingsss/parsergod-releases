"""Фоновый сбор: запускает ядро parser_2gis по списку «город × запрос»."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from parser_2gis.chrome import ChromeRemote
from parser_2gis.chrome.exceptions import ChromePathNotFound
from parser_2gis.chrome.options import ChromeOptions
from parser_2gis.logger import logger as core_logger
from parser_2gis.parser import get_parser
from parser_2gis.parser.options import ParserOptions
from parser_2gis.writer.writers import FileWriter
from parser_2gis.writer.options import WriterOptions

from .browsers import pick_browser
from .excel import write_xlsx
from .records import parse_doc
from .storage import data_dir, safe_filename

# Признаки страницы проверки «вы не робот» у 2ГИС.
_CAPTCHA_JS = r'''(function(){
  var t = document.body ? document.body.innerText : '';
  return location.href.indexOf('back_location=') >= 0 ||
         /не с роботом|дополнительная проверка|пройдите капчу/i.test(t);
})()'''
_RESULTS_JS = 'document.querySelectorAll(\'a[href*="/firm/"]\').length'
CAPTCHA_WAIT_SEC = 600

def build_url(city: Dict[str, str], query: str) -> str:
    text = query.strip()
    if city.get('locality'):  # небольшой город внутри региона 2ГИС: «кофейня Полевской»
        text = '%s %s' % (text, city['locality'])
    return 'https://2gis.%s/%s/search/%s' % (
        city['domain'], city['code'], urllib.parse.quote(text, safe=''))


def _rname(city: Dict[str, Any], code: str) -> str:
    """Название региона 2ГИС по коду (для журнала)."""
    return (city.get('alt_names') or {}).get(code, code)


def _norm(s: str) -> str:
    return (s or '').lower().replace('ё', 'е').strip()


@dataclass
class Task:
    city: Dict[str, str]
    query: str

    @property
    def label(self) -> str:
        return '%s — %s' % (self.city['name'], self.query)

    @property
    def url(self) -> str:
        return build_url(self.city, self.query)


@dataclass
class Params:
    tasks: List[Task]
    fields: List[str]
    limit: Optional[int] = 100                # на каждый запрос; None = все
    require_any: List[str] = field(default_factory=list)  # нужен хотя бы один из этих контактов
    require_phone: bool = False
    one_per_chain: bool = False                # без сетевых дублей
    min_rating: float = 0.0
    no_site: bool = False                      # только без нормального сайта (нет, соцсети, конструктор)
    show_browser: bool = False
    delay_ms: int = 0
    browser_path: Optional[str] = None
    output_dir: str = ''
    source: str = '2gis'                       # '2gis' | 'yandex'


class _LimitReached(Exception):
    pass


class _WrongRegion(_LimitReached):
    """Регион 2ГИС выдаёт организации, но ни одной из нужного населённого пункта."""


# Небольшой город ищется внутри региона 2ГИС («автосервис Полевской» в Екатеринбурге).
# Сколько карточек подряд «не из того города» терпим:
WRONG_REGION_AFTER = 25   # ...пока не нашли ни одной своей → регион не тот, пробуем следующий
LOCALITY_DONE_AFTER = 40  # ...после того как свои уже были → свои закончились, 2ГИС подмешивает соседей


class _Cancelled(Exception):
    pass


class _Collector(FileWriter):
    """Получает документы от ядра, фильтрует и копит строки."""

    def __init__(self, job: 'Job') -> None:
        super().__init__('', WriterOptions())
        self.job = job

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def write(self, catalog_doc: Any) -> None:
        if self.job._cancel.is_set():
            raise _Cancelled()
        self.job._seen_docs += 1
        try:
            err = catalog_doc.get('meta', {}).get('error')
        except AttributeError:
            err = None
        if err:
            self.job._server_errors += 1
            self.job._last_server_error = err.get('type') or err.get('message')
            if self.job._server_errors in (5, 30):
                self.job.log('error', '2ГИС отвечает ошибкой на карточки (%s). Не сворачивайте и не перекрывайте окно браузера.'
                             % self.job._last_server_error)
            return
        rec = parse_doc(catalog_doc)
        if rec is None:
            return
        self.job._accept(rec)


class _UiLogHandler(logging.Handler):
    """Пересылает ошибки ядра в журнал окна (без технического мусора)."""

    def __init__(self, sink: Callable[[str, str], None]) -> None:
        super().__init__(level=logging.WARNING)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if 'Документ каталога' in msg:  # огромные дампы JSON не показываем
                msg = msg.split('\n')[0]
            self.sink('warn' if record.levelno < logging.ERROR else 'error', msg[:300])
        except Exception:
            pass


class Job:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._parser = None
        self.reset()

    # ---------- состояние ----------
    def reset(self) -> None:
        self.state = 'idle'          # idle | running | done | stopped | error
        self.rows: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        self.error: Optional[str] = None
        self.file_path: Optional[str] = None
        self.params: Optional[Params] = None
        self.task_index = 0
        self.task_count = 0
        self.task_label = ''
        self.started_at = 0.0
        self.finished_at = 0.0
        self._task_kept = 0
        self._seen_ids: set = set()
        self._seen_orgs: set = set()
        self._seen_docs = 0
        self._skipped = 0
        self._other_place = 0
        self._miss_streak = 0
        self._local_hits = 0
        self._server_errors = 0
        self._last_server_error = None
        self.attention: Optional[str] = None   # просьба к пользователю (пройти проверку)
        self.needs_visible = False             # 2ГИС требует проверку → дальше сразу видимый браузер

    @property
    def running(self) -> bool:
        return self.state == 'running'

    def log(self, level: str, text: str) -> None:
        with self._lock:
            self.logs.append({'t': time.strftime('%H:%M:%S'), 'level': level, 'text': text})
            if len(self.logs) > 3000:
                del self.logs[:1000]

    # ---------- управление ----------
    def start(self, params: Params) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError('Сбор уже идёт')
            self.reset()
            self.params = params
            self.state = 'running'
            self.task_count = len(params.tasks)
            self.started_at = time.time()
            self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()
        gate = getattr(self, '_gate_remote', None)
        if gate is not None:
            try:
                gate.stop()
            except Exception:
                pass
        ys = getattr(self, '_ysearch', None)
        if ys is not None:
            ys.close()
        parser = self._parser
        if parser is not None:
            try:
                parser.close()
            except Exception:
                pass

    def wait(self, timeout: float = 10) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ---------- фильтры ----------
    def _accept(self, rec: Dict[str, Any]) -> None:
        p = self.params
        assert p is not None
        dup = bool(rec['_id'] and rec['_id'] in self._seen_ids)  # точка уже нашлась по другому запросу
        locality = p.tasks[self.task_index].city.get('locality')
        if locality and _norm(locality) not in {_norm(x) for x in rec.get('_places') or []}:
            if not dup:
                self._other_place += 1
            self._miss_streak += 1
            if p.source != 'yandex':
                if not self._local_hits and self._miss_streak >= WRONG_REGION_AFTER:
                    raise _WrongRegion()
                if self._local_hits and self._miss_streak >= LOCALITY_DONE_AFTER:
                    raise _LimitReached()
            return  # 2ГИС подмешал организации из соседних городов региона
        self._miss_streak = 0
        self._local_hits += 1
        if dup:
            return
        if p.require_phone and not rec.get('phone'):
            self._skipped += 1
            return
        if p.require_any and not any(rec.get(k) for k in p.require_any):
            self._skipped += 1
            return
        if p.min_rating and (rec.get('rating') or 0) < p.min_rating:
            self._skipped += 1
            return
        if p.no_site and rec.get('site_kind') == 'Есть сайт':
            self._skipped += 1
            return
        if p.one_per_chain and rec.get('_org'):
            if rec['_org'] in self._seen_orgs:
                self._skipped += 1
                return
            self._seen_orgs.add(rec['_org'])
        if rec['_id']:
            self._seen_ids.add(rec['_id'])

        task = p.tasks[self.task_index]
        if rec['_id'] and p.source != 'yandex':  # ссылка на карточку в «родном» домене: 2gis.ru/ekaterinburg/firm/...
            rec['url'] = 'https://2gis.%s/%s/firm/%s' % (
                task.city['domain'], rec.get('_alias') or task.city['code'], rec['_id'])
        rec['_query'] = task.query if len({t.query for t in p.tasks}) > 1 else None
        if len({t.city.get('key', t.city['code']) for t in p.tasks}) > 1:
            rec['_query'] = task.label
        with self._lock:
            self.rows.append(rec)
        self._task_kept += 1
        if p.limit and self._task_kept >= p.limit:
            raise _LimitReached()

    # ---------- основной цикл ----------
    def _run(self) -> None:
        p = self.params
        assert p is not None
        handler = _UiLogHandler(self.log)
        core_logger.addHandler(handler)
        if core_logger.level == logging.NOTSET or core_logger.level > logging.WARNING:
            core_logger.setLevel(logging.WARNING)
        failed = 0
        final = 'error'
        try:
            browser = pick_browser(p.browser_path)
            if not browser:
                raise ChromePathNotFound()
            self.log('info', 'Браузер: %s' % browser[0])

            # Постоянный профиль: куки (в т.ч. после пройденной проверки) живут между запусками.
            profile = data_dir() / ('browser-' + (re.sub(r'\W+', '-', browser[1].lower()).strip('-')[-40:] or 'default'))
            chrome = ChromeOptions(binary_path=browser[1], headless=not p.show_browser,
                                   disable_images=True, silent_browser=True,
                                   start_maximized=False, profile_path=profile)
            popts = ParserOptions(max_records=100000, delay_between_clicks=max(0, int(p.delay_ms)),
                                  skip_404_response=True)
            collector = _Collector(self)

            if p.source == 'yandex':
                failed = self._run_yandex(p, chrome)
                p = None  # цикл 2ГИС ниже не нужен
            elif not self._gate(chrome, p.tasks[0].url):
                if not self._cancel.is_set():
                    raise RuntimeError('2ГИС запросил проверку «вы не робот», и она не была пройдена.')

            for i, task in enumerate(p.tasks if p else []):
                if self._cancel.is_set():
                    break
                self.task_index = i
                self.task_label = task.label
                before = len(self.rows)
                locality = task.city.get('locality')
                # Небольшой город: перебираем регионы-кандидаты, пока не найдём тот, куда он входит.
                regions = [task.city['code']]
                if locality and not task.city.get('confirmed'):
                    regions = list(task.city.get('alts') or regions)
                for r_idx, region in enumerate(regions):
                    if self._cancel.is_set():
                        break
                    if locality:
                        task.city['code'] = region
                        task.city['region'] = _rname(task.city, region)
                        self.log('info', 'Ищу: %s (в регионе 2ГИС «%s»)' % (task.label, task.city['region']))
                    else:
                        self.log('info', 'Ищу: %s' % task.label)
                    self._task_kept = 0
                    self._miss_streak = 0
                    self._local_hits = 0
                    wrong_region = False
                    for attempt in (1, 2):
                        try:
                            self._parser = get_parser(task.url, chrome_options=chrome, parser_options=popts)
                            with self._parser:
                                self._parser.parse(collector)
                            break
                        except _WrongRegion:
                            wrong_region = True
                            break
                        except _LimitReached:
                            break
                        except _Cancelled:
                            break
                        except ChromePathNotFound:
                            raise
                        except Exception as e:  # одна ссылка не должна валить весь сбор
                            if self._cancel.is_set():
                                break
                            self._parser = None
                            if attempt == 1 and self._gate(chrome, task.url, mid_run=True):
                                self.log('info', 'Повторяю «%s»' % task.label)
                                continue
                            failed += 1
                            self.log('error', 'Не удалось обработать «%s»: %s' % (task.label, _human_error(e)))
                        finally:
                            self._parser = None
                    if not locality or self._local_hits or len(self.rows) > before:
                        if locality:  # регион верный — следующие запросы по этому городу сразу идут туда
                            task.city['confirmed'] = True
                        break
                    if r_idx + 1 < len(regions):
                        self.log('info', '«%s» не нашёлся в регионе «%s»%s — пробую «%s»' % (
                            locality, task.city['region'], ' (там только соседние города)' if wrong_region else '',
                            _rname(task.city, regions[r_idx + 1])))
                got = len(self.rows) - before
                self.log('ok' if got else 'warn', '%s: добавлено %d' % (task.label, got)
                         + ('' if got else ' (ничего нового или нет результатов)'))

            p = self.params
            final = 'stopped' if self._cancel.is_set() else 'done'
            if failed and failed == len(p.tasks) and not self.rows:
                final = 'error'
                self.error = 'Ни один запрос не обработан. Проверьте интернет и попробуйте включить «Показывать браузер».'
        except ChromePathNotFound:
            final = 'error'
            self.error = ('Не найден браузер Chrome / Edge / Яндекс.Браузер. '
                          'Установите Google Chrome или укажите путь к браузеру в настройках.')
        except Exception as e:
            final = 'error'
            self.error = _human_error(e)
            core_logger.exception('Сбой сбора')
        finally:
            core_logger.removeHandler(handler)
            self.finished_at = time.time()
            if self.rows:
                try:
                    self.file_path = self._save(p)
                    self.log('ok', 'Таблица сохранена: %s' % self.file_path)
                except Exception as e:
                    self.log('error', 'Не удалось сохранить Excel: %s' % e)
            if self._skipped:
                self.log('info', 'Отсеяно фильтрами: %d' % self._skipped)
            if self._other_place:
                self.log('info', 'Пропущено организаций из других населённых пунктов: %d' % self._other_place)
            self.finished_at = time.time()
            self.state = final  # последним шагом — окно увидит уже сохранённый файл

    def _run_yandex(self, p: Params, chrome: ChromeOptions) -> int:
        """Сбор с Яндекс Карт. Возвращает число неудавшихся запросов."""
        from .yandex import YandexSearch
        chrome.headless = not p.show_browser and os.environ.get('PARSER_YANDEX_HEADLESS') == '1'
        failed = 0
        for i, task in enumerate(p.tasks):
            if self._cancel.is_set():
                break
            self.task_index, self.task_label, self._task_kept = i, task.label, 0
            before = len(self.rows)
            place = task.city.get('locality') or task.city['name']
            self.log('info', 'Ищу на Яндекс Картах: %s' % task.label)

            def on_record(rec: Dict[str, Any]) -> None:
                if self._cancel.is_set():
                    raise _Cancelled()
                self._seen_docs += 1
                rec['city'] = rec.get('city') or place
                self._accept(rec)

            def on_captcha(msg: Optional[str]) -> None:
                self.attention = msg
                if msg:
                    self.needs_visible = True

            ys = YandexSearch(chrome, '%s %s' % (task.query, place), on_record,
                              self._cancel.is_set, self.log, on_captcha,
                              debug=os.environ.get('PARSER_DEBUG') == '1')
            self._ysearch = ys
            try:
                ys.run_city(task.query, place)
            except (_LimitReached, _Cancelled):
                pass
            except Exception as e:
                if not self._cancel.is_set():
                    failed += 1
                    self.log('error', 'Не удалось обработать «%s»: %s' % (task.label, _human_error(e)))
            finally:
                ys.close()
                self._ysearch = None
            got = len(self.rows) - before
            self.log('ok' if got else 'warn', '%s: добавлено %d' % (task.label, got)
                     + ('' if got else ' (ничего нового или нет результатов)'))
        return failed

    def _gate(self, chrome: ChromeOptions, url: str, mid_run: bool = False) -> bool:
        """Открывает поиск и проверяет, не просит ли 2ГИС пройти проверку.

        Если просит — показывает браузер и ждёт, пока ПОЛЬЗОВАТЕЛЬ сам пройдёт её
        (программа капчу не решает). Возвращает True, когда результаты поиска видны.
        Если проверка встретилась, дальше сбор идёт в видимом браузере.
        """
        force_headless = os.environ.get('PARSER_FORCE_HEADLESS') == '1'  # только для автотестов
        for visible_pass in (False, True):
            if visible_pass:
                if force_headless:
                    pass
                elif chrome.headless:
                    chrome.headless = False
                    self.needs_visible = True
                    self.log('warn', 'Открываю браузер, чтобы вы могли пройти проверку.')
            cr = ChromeRemote(chrome_options=chrome, response_patterns=[])
            self._gate_remote = cr
            try:
                cr.start()
                cr.navigate(url, referer='https://google.com', timeout=60)
                started = time.time()
                asked = False
                # Скрытый браузер проверяем 20 с; в видимом ждём человека до 10 минут.
                limit = 20 if (chrome.headless and not visible_pass and not force_headless) else CAPTCHA_WAIT_SEC
                while time.time() - started < limit and not self._cancel.is_set():
                    time.sleep(1.5)
                    try:
                        captcha = bool(cr.execute_script(_CAPTCHA_JS))
                        results = int(cr.execute_script(_RESULTS_JS) or 0)
                    except Exception:
                        continue  # страница перезагружается
                    if results > 0 and not captcha:
                        if asked:
                            self.log('ok', 'Проверка пройдена, продолжаю сбор.')
                        return True
                    if captcha:
                        if not visible_pass and chrome.headless and not force_headless:
                            break  # перезапустим видимым
                        if not asked:
                            asked = True
                            cr.bring_to_front()
                            self.attention = ('2ГИС просит подтвердить, что вы не робот. Пройдите проверку '
                                              'в открывшемся окне браузера — сбор продолжится сам.')
                            self.log('warn', '2ГИС показал проверку «вы не робот» — ждём, пока вы её пройдёте.')
                    elif time.time() - started > 15 and not asked:
                        return True  # проверки нет, просто пустая выдача — пусть решает парсер
                else:
                    if self._cancel.is_set():
                        return False
                    self.log('error', 'Проверка 2ГИС не пройдена за %d мин.' % (CAPTCHA_WAIT_SEC // 60))
                    return False
            except Exception as e:
                if self._cancel.is_set():
                    return False
                self.log('error', 'Не удалось открыть 2ГИС: %s' % _human_error(e))
                return False
            finally:
                self.attention = None
                self._gate_remote = None
                try:
                    cr.stop()
                except Exception:
                    pass
        return False

    def _save(self, p: Params, path: Optional[str] = None) -> str:
        if not path:
            os.makedirs(p.output_dir, exist_ok=True)
            cities = list(dict.fromkeys(t.city['name'] for t in p.tasks))
            queries = list(dict.fromkeys(t.query for t in p.tasks))
            name = '%s — %s' % (', '.join(cities[:2]) + ('…' if len(cities) > 2 else ''),
                                ', '.join(queries[:2]) + ('…' if len(queries) > 2 else ''))
            stamp = dt.datetime.now().strftime('%Y-%m-%d %H-%M')
            if p.source == 'yandex':
                name = 'Яндекс — ' + name
            path = os.path.join(p.output_dir, safe_filename('%s — %s' % (name, stamp)) + '.xlsx')
        multi = any(r.get('_query') for r in self.rows)
        write_xlsx(path, self.rows, p.fields, add_query_column=multi)
        return path

    def save_as(self, path: str) -> str:
        assert self.params is not None
        return self._save(self.params, path)


def _human_error(e: Exception) -> str:
    text = str(e) or e.__class__.__name__
    if isinstance(e, TimeoutError) or 'Timeout' in text:
        return '2ГИС долго не отвечает (таймаут). Попробуйте ещё раз или включите «Показывать браузер».'
    if 'Connection' in text or 'WebSocket' in text:
        return 'Потеряна связь с браузером.'
    return text[:300]

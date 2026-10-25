"""Склейка нескольких выгрузок в одну таблицу без дублей.

Читает xlsx, которые сделала сама программа (заголовки = FIELDS[...]['title']),
стандартной библиотекой — openpyxl не нужен. Гиперссылки восстанавливаются
из связей листа: в ячейке лежит подпись («Открыть в Яндексе»), а настоящий
адрес — в sheet1.xml.rels.
"""
from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from .excel import write_xlsx
from .records import FIELDS, format_phone

_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
       'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
       'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
_R_ID = '{%s}id' % _NS['r']

_TITLE_TO_KEY = {v['title']: k for k, v in FIELDS.items()}
_TITLE_TO_KEY['Запрос'] = '_query'
_NUMERIC = {'rating': float, 'reviews': int, 'branches': int}


class MergeError(Exception):
    pass


def _col_index(ref: str) -> int:
    """'C12' -> 2."""
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):  # type: ignore[union-attr]
        n = n * 26 + ord(ch) - 64
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    except KeyError:
        return []
    # Строка может быть разбита на несколько <r><t> (форматирование внутри ячейки).
    return [''.join(t.text or '' for t in si.iter('{%s}t' % _NS['m']))
            for si in root.findall('m:si', _NS)]


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    wb = ET.fromstring(zf.read('xl/workbook.xml'))
    sheet = wb.find('m:sheets/m:sheet', _NS)
    if sheet is None:
        raise MergeError('в книге нет листов')
    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    for rel in rels.findall('rel:Relationship', _NS):
        if rel.get('Id') == sheet.get(_R_ID):
            target = rel.get('Target', '')
            return target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target)
    raise MergeError('не найден файл первого листа')


def _hyperlinks(zf: zipfile.ZipFile, sheet_path: str, root: ET.Element) -> Dict[str, str]:
    """{'D5': 'https://...'} для ячеек с внешними ссылками."""
    rels_path = posixpath.join(posixpath.dirname(sheet_path), '_rels',
                               posixpath.basename(sheet_path) + '.rels')
    try:
        rels = ET.fromstring(zf.read(rels_path))
    except KeyError:
        return {}
    targets = {r.get('Id'): r.get('Target', '') for r in rels.findall('rel:Relationship', _NS)}
    out: Dict[str, str] = {}
    for h in root.iterfind('m:hyperlinks/m:hyperlink', _NS):
        url = targets.get(h.get(_R_ID))
        if url and h.get('ref'):
            out[h.get('ref').split(':')[0]] = url  # type: ignore[union-attr]
    return out


def _cell_value(c: ET.Element, shared: List[str]) -> str:
    kind = c.get('t')
    if kind == 'inlineStr':
        return ''.join(t.text or '' for t in c.iter('{%s}t' % _NS['m']))
    v = c.find('m:v', _NS)
    if v is None or v.text is None:
        return ''
    if kind == 's':
        idx = int(v.text)
        return shared[idx] if 0 <= idx < len(shared) else ''
    return v.text


def read_xlsx(path: Path) -> List[Dict[str, Any]]:
    """Строки первого листа как словари {field_key: значение}."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise MergeError('%s: не открывается как xlsx (%s)' % (path.name, e)) from e
    with zf:
        shared = _shared_strings(zf)
        sheet_path = _first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))
        links = _hyperlinks(zf, sheet_path, root)

        grid: List[Dict[int, Tuple[str, str]]] = []
        for row in root.iterfind('m:sheetData/m:row', _NS):
            cells: Dict[int, Tuple[str, str]] = {}
            for c in row.findall('m:c', _NS):
                ref = c.get('r', '')
                if ref:
                    cells[_col_index(ref)] = (ref, _cell_value(c, shared))
            grid.append(cells)

    if not grid:
        return []
    header = {i: _TITLE_TO_KEY.get(v.strip()) for i, (_, v) in grid[0].items()}
    if 'name' not in header.values():
        raise MergeError('%s: нет колонки «%s» — это не выгрузка программы'
                         % (path.name, FIELDS['name']['title']))

    rows: List[Dict[str, Any]] = []
    for cells in grid[1:]:
        rec: Dict[str, Any] = {}
        for i, (ref, val) in cells.items():
            key = header.get(i)
            if not key:
                continue
            val = links.get(ref) if ref in links and not links[ref].startswith('mailto:') else val
            rec[key] = _typed(key, val.strip()) if val else None
        if rec.get('name'):
            rows.append(rec)
    return rows


def _typed(key: str, val: str) -> Any:
    conv = _NUMERIC.get(key)
    if conv is None:
        return val
    try:
        return conv(float(val)) if conv is int else conv(val)
    except ValueError:
        return val


def _norm(text: Optional[str]) -> str:
    return re.sub(r'[^\w]+', ' ', (text or '').lower().replace('ё', 'е')).strip()


def _phones(rec: Dict[str, Any]) -> List[str]:
    out = []
    for p in str(rec.get('phone') or '').split(','):
        digits = re.sub(r'\D', '', format_phone(p))
        if len(digits) >= 10:
            out.append(digits[-10:])  # без кода страны: +7 и 8 — один номер
    return out


def _keys(rec: Dict[str, Any]) -> List[str]:
    """Признаки, по любому из которых две строки считаются одной организацией."""
    keys = []
    url = str(rec.get('url') or '')
    m = re.search(r'/firm/(\d+)', url)
    if m:
        keys.append('id:' + m.group(1))
    name = _norm(rec.get('name'))
    if name and rec.get('address'):
        keys.append('na:%s|%s' % (name, _norm(rec.get('address'))))
    keys.extend('ph:%s|%s' % (name, p) for p in _phones(rec))
    return keys


def _absorb(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    """Дополняет base полями из extra; списки контактов объединяются."""
    for k, v in extra.items():
        if v in (None, ''):
            continue
        cur = base.get(k)
        if cur in (None, ''):
            base[k] = v
        elif k in FIELDS and FIELDS[k]['group'] == 'contact' or k == '_query':
            have = [x.strip() for x in str(cur).split(',')]
            add = [x.strip() for x in str(v).split(',') if x.strip() not in have]
            if add:
                base[k] = ', '.join(have + add)
        elif k == 'reviews' and isinstance(v, int) and isinstance(cur, int) and v > cur:
            # Более свежая выгрузка обычно с бо́льшим числом отзывов — берём её рейтинг.
            base['reviews'] = v
            if isinstance(extra.get('rating'), (int, float)):
                base['rating'] = extra['rating']


def dedupe(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    for rec in rows:
        keys = _keys(rec)
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            hit = len(merged)
            merged.append(dict(rec))
        else:
            _absorb(merged[hit], rec)
        for k in _keys(merged[hit]):
            index.setdefault(k, hit)
    return merged


def merge_files(paths: List[Path], out: Path) -> Tuple[int, int]:
    """Возвращает (строк прочитано, строк записано)."""
    rows: List[Dict[str, Any]] = []
    for p in paths:
        part = read_xlsx(p)
        stem = p.stem
        for r in part:
            r.setdefault('_query', stem)
            if not r.get('_query'):
                r['_query'] = stem
        rows.extend(part)
    if not rows:
        raise MergeError('во входных файлах нет ни одной строки')
    result = dedupe(rows)
    fields = [k for k in FIELDS if any(r.get(k) not in (None, '') for r in result)]
    out.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(str(out), result, fields, add_query_column=True)
    return len(rows), len(result)


def _collect(args: List[str]) -> List[Path]:
    paths: List[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths.extend(sorted(x for x in p.glob('*.xlsx') if not x.name.startswith('~$')))
        elif p.is_file():
            paths.append(p)
        else:
            raise MergeError('не найден путь: %s' % a)
    return paths


def run(argv: List[str]) -> int:
    """python -m app.main --merge OUT.xlsx IN.xlsx|ПАПКА [...]"""
    if len(argv) < 2:
        print('использование: --merge ВЫХОД.xlsx ФАЙЛ_или_ПАПКА [...]', file=sys.stderr)
        return 2
    out = Path(argv[0])
    try:
        inputs = [p for p in _collect(argv[1:]) if p.resolve() != out.resolve()]
        if not inputs:
            raise MergeError('нет входных .xlsx')
        total, kept = merge_files(inputs, out)
    except (MergeError, OSError, ET.ParseError) as e:
        print('ошибка: %s' % e, file=sys.stderr)
        return 1
    print('файлов: %d, строк: %d, после склейки: %d, дублей убрано: %d -> %s'
          % (len(inputs), total, kept, total - kept, out))
    return 0

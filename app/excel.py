"""Запись результатов в аккуратную Excel-таблицу."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from xlsxwriter.workbook import Workbook

from .records import FIELDS

_MAX_WIDTH = 55
_LINK_KEYS = {'website', 'vkontakte', 'instagram', 'telegram', 'whatsapp',
              'viber', 'odnoklassniki', 'youtube', 'url', 'yandex'}
_LINK_LABELS = {'yandex': 'Открыть в Яндексе'}


def write_xlsx(path: str, rows: List[Dict[str, Any]], fields: Sequence[str],
               add_query_column: bool = False, drop_empty: bool = True,
               extra: Sequence[tuple] = ()) -> int:
    """Пишет `rows` в `path`. Возвращает количество строк."""
    cols = [f for f in fields if f in FIELDS]
    if 'name' not in cols:
        cols.insert(0, 'name')
    if drop_empty and rows:
        cols = [c for c in cols if c == 'name' or any(r.get(c) not in (None, '') for r in rows)]

    headers = [FIELDS[c]['title'] for c in cols]
    keys = list(cols)
    if add_query_column:
        headers = ['Запрос'] + headers
        keys = ['_query'] + keys
    for key, title in extra:  # доп. колонки в конце (статус и заметка из базы лидов)
        headers.append(title)
        keys.append(key)

    with Workbook(path, {'strings_to_urls': False, 'strings_to_numbers': False}) as wb:
        ws = wb.add_worksheet('Организации')
        head = wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#1F6F5C',
                              'border': 1, 'border_color': '#1F6F5C', 'valign': 'vcenter'})
        link = wb.add_format({'font_color': '#0563C1', 'underline': 1})
        num = wb.add_format({'num_format': '0.0'})

        widths = [len(h) + 2 for h in headers]
        ws.set_row(0, 22)
        for c, h in enumerate(headers):
            ws.write(0, c, h, head)

        for r, row in enumerate(rows, start=1):
            for c, key in enumerate(keys):
                val = row.get(key)
                if val in (None, ''):
                    continue
                if key == 'rating' and isinstance(val, (int, float)):
                    ws.write_number(r, c, float(val), num)
                    text = '0.0'
                elif key in ('reviews', 'branches') and isinstance(val, int):
                    ws.write_number(r, c, val)
                    text = str(val)
                else:
                    text = str(val)
                    first = text.split(', ')[0]
                    if key in _LINK_KEYS and first.startswith(('http://', 'https://')) and len(first) < 255:
                        text = _LINK_LABELS.get(key, text)
                        try:
                            ws.write_url(r, c, first, link, string=text)
                        except Exception:
                            ws.write_string(r, c, text)
                    elif key == 'email' and '@' in first:
                        ws.write_url(r, c, 'mailto:' + first, link, string=text)
                    else:
                        ws.write_string(r, c, text)
                widths[c] = max(widths[c], min(len(text), _MAX_WIDTH))

        for c, w in enumerate(widths):
            ws.set_column(c, c, min(w + 2, _MAX_WIDTH))
        ws.freeze_panes(1, 1 if not add_query_column else 2)
        ws.autofilter(0, 0, max(len(rows), 1), len(headers) - 1)
    return len(rows)

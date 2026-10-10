"""Пути и сохранение настроек пользователя."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict

from .brand import APP_NAME


def _windows_known_folder(csidl: int) -> str | None:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)  # type: ignore
        return buf.value or None
    except Exception:
        return None


def data_dir() -> Path:
    """Папка для настроек (не видна пользователю)."""
    if sys.platform.startswith('win'):
        base = _windows_known_folder(28) or os.environ.get('LOCALAPPDATA') or str(Path.home())
    elif sys.platform == 'darwin':
        base = str(Path.home() / 'Library' / 'Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_dir() -> Path:
    """«Документы/ParserGod» — туда по умолчанию кладутся таблицы."""
    docs = None
    if sys.platform.startswith('win'):
        docs = _windows_known_folder(5)  # CSIDL_PERSONAL, учитывает перенос в OneDrive
    if not docs:
        cand = Path.home() / 'Documents'
        docs = str(cand if cand.is_dir() else Path.home())
    return Path(docs) / APP_NAME


def safe_filename(name: str, limit: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return (name[:limit].rstrip(' .')) or 'Выгрузка'


_SETTINGS_FILE = 'settings.json'


def load_settings() -> Dict[str, Any]:
    try:
        with open(data_dir() / _SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: Dict[str, Any]) -> None:
    try:
        tmp = data_dir() / (_SETTINGS_FILE + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, data_dir() / _SETTINGS_FILE)
    except OSError:
        pass

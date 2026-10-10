"""Поиск Chromium-браузера на компьютере клиента.

Ядру парсера нужен любой браузер на движке Chromium с поддержкой DevTools
(Chrome, Edge, Яндекс.Браузер, Brave, Chromium). Edge предустановлен на всех
Windows 10/11, поэтому клиенту на Windows обычно ничего ставить не нужно.
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Tuple

Browser = Tuple[str, str]  # (человеческое имя, путь к исполняемому файлу)


def _windows_candidates() -> List[Browser]:
    roots = []
    for var in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'LOCALAPPDATA'):
        val = os.environ.get(var)
        if val and val not in roots:
            roots.append(val)

    rel = [
        ('Google Chrome', r'Google\Chrome\Application\chrome.exe'),
        ('Microsoft Edge', r'Microsoft\Edge\Application\msedge.exe'),
        ('Яндекс.Браузер', r'Yandex\YandexBrowser\Application\browser.exe'),
        ('Brave', r'BraveSoftware\Brave-Browser\Application\brave.exe'),
        ('Chromium', r'Chromium\Application\chrome.exe'),
    ]
    out: List[Browser] = []
    for name, sub in rel:
        for root in roots:
            out.append((name, os.path.join(root, sub)))

    # Реестр: App Paths знает, куда реально установлены Chrome / Edge.
    try:
        import winreg  # type: ignore
        for exe, name in (('chrome.exe', 'Google Chrome'), ('msedge.exe', 'Microsoft Edge')):
            key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s' % exe
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ) as k:
                        out.append((name, winreg.QueryValue(k, None)))
                except OSError:
                    continue
    except ImportError:
        pass
    return out


def _mac_candidates() -> List[Browser]:
    apps = [
        ('Google Chrome', 'Google Chrome.app/Contents/MacOS/Google Chrome'),
        ('Microsoft Edge', 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
        ('Яндекс.Браузер', 'Yandex.app/Contents/MacOS/Yandex'),
        ('Brave', 'Brave Browser.app/Contents/MacOS/Brave Browser'),
        ('Chromium', 'Chromium.app/Contents/MacOS/Chromium'),
    ]
    out: List[Browser] = []
    for name, sub in apps:
        for root in ('/Applications', os.path.expanduser('~/Applications')):
            out.append((name, os.path.join(root, sub)))
    return out


def _linux_candidates() -> List[Browser]:
    out: List[Browser] = []
    for name, exe in (('Google Chrome', 'google-chrome'), ('Google Chrome', 'google-chrome-stable'),
                      ('Chromium', 'chromium'), ('Chromium', 'chromium-browser'),
                      ('Microsoft Edge', 'microsoft-edge'), ('Яндекс.Браузер', 'yandex-browser'),
                      ('Brave', 'brave-browser')):
        path = shutil.which(exe)
        if path:
            out.append((name, path))
    env = os.environ.get('PARSER_BROWSER')
    if env:
        out.insert(0, ('Chromium', env))
    return out


def find_browsers() -> List[Browser]:
    """Все найденные браузеры в порядке предпочтения, без дублей."""
    if sys.platform.startswith('win'):
        cands = _windows_candidates()
    elif sys.platform == 'darwin':
        cands = _mac_candidates()
    else:
        cands = _linux_candidates()

    seen = set()
    found: List[Browser] = []
    for name, path in cands:
        if not path:
            continue
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen or not os.path.isfile(path):
            continue
        seen.add(norm)
        found.append((name, path))
    return found


def pick_browser(custom_path: Optional[str] = None) -> Optional[Browser]:
    """Браузер для работы: путь из настроек, иначе первый найденный."""
    if custom_path and os.path.isfile(custom_path):
        return ('Свой путь', custom_path)
    found = find_browsers()
    return found[0] if found else None

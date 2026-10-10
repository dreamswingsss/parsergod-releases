"""Точка входа десктоп-приложения."""
from __future__ import annotations

import os
import sys


def _fix_std_streams() -> None:
    # В оконной сборке PyInstaller stdout/stderr = None, а некоторые
    # библиотеки пишут туда — перенаправляем в никуда.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')


def _ui_path() -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(base, 'app', 'ui', 'index.html'),
                  os.path.join(base, 'ui', 'index.html')]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[-1]


def main() -> None:
    _fix_std_streams()

    if '--selftest' in sys.argv:
        from .selftest import run
        sys.exit(run())

    import webview

    from . import brand
    from .api import Api

    api = Api()
    window = webview.create_window(
        brand.APP_TITLE, url=_ui_path(), js_api=api,
        width=1180, height=800, min_size=(960, 660), background_color='#F5F7FA',
    )
    api.attach(window)
    window.events.closing += api.shutdown
    webview.start(debug='--debug' in sys.argv, private_mode=True)


if __name__ == '__main__':
    main()

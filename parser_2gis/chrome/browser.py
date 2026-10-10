from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from ..common import wait_until_finished
from ..logger import logger
from .exceptions import ChromePathNotFound
from .utils import free_port, locate_chrome_path

if TYPE_CHECKING:
    from .options import ChromeOptions


class ChromeBrowser():
    """Chrome Browser with temporary profile.

    Args:
        chrome_options: Chrome options.
    """
    def __init__(self, chrome_options: ChromeOptions) -> None:
        binary_path = (chrome_options.binary_path
                       if chrome_options.binary_path else locate_chrome_path())

        if not binary_path:
            raise ChromePathNotFound

        logger.debug('Запуск Chrome Браузера.')

        self._persistent_profile = chrome_options.profile_path is not None
        if self._persistent_profile:
            self._profile_path = str(chrome_options.profile_path)
            os.makedirs(self._profile_path, exist_ok=True)
        else:
            self._profile_path = tempfile.mkdtemp()
        self._remote_port = free_port()
        self._chrome_cmd = [
            binary_path,
            f'--remote-debugging-port={self._remote_port}',
            f'--user-data-dir={self._profile_path}', '--no-default-browser-check',
            '--no-first-run', '--no-sandbox', '--disable-fre',
            '--remote-allow-origins=*',
            f'--js-flags=--expose-gc --max-old-space-size={chrome_options.memory_limit}',
        ]

        # Доп. флаги из окружения (отладка, прокси): PARSER_CHROME_ARGS="--flag1 --flag2"
        extra = shlex.split(os.environ.get('PARSER_CHROME_ARGS', ''))
        self._chrome_cmd.extend(a for a in extra if a.startswith('--'))

        # Keep rendering when the window is in the background / covered by other
        # windows: otherwise Chrome pauses the WebGL map and 2GIS answers
        # "Bound is incorrect" for every card.
        self._chrome_cmd += [
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-background-timer-throttling',
            '--disable-features=CalculateNativeWinOcclusion',
        ]
        if not chrome_options.headless:
            self._chrome_cmd.append('--window-size=1280,860')

        if chrome_options.start_maximized:
            self._chrome_cmd.append('--start-maximized')

        if chrome_options.headless:
            logger.debug('В Chrome установлен в скрытый режим.')
            # Use the "new" headless mode: it renders the WebGL map (mapgl)
            # that 2GIS relies on. The legacy `--headless` + `--disable-gpu`
            # combo kept the map from laying out, so the page sent a degenerate
            # map viewport (viewpoint1 == viewpoint2) that the catalog API now
            # rejects with HTTP 400 "Bound is incorrect". The tab also needs a
            # real, large viewport — enforced via Emulation.setDeviceMetricsOverride
            # in ChromeRemote._setup_tab (the --window-size flag alone does not
            # reach the DevTools-created tab).
            self._chrome_cmd.append('--headless=new')
            self._chrome_cmd.append('--window-size=1920,1080')

        if chrome_options.disable_images:
            logger.debug('В Chrome отключены изображения.')
            self._chrome_cmd.append('--blink-settings=imagesEnabled=false')

        if chrome_options.silent_browser:
            logger.debug('В Chrome отключен вывод отладочной информации.')
            self._proc = subprocess.Popen(self._chrome_cmd, shell=False,
                                          stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        else:
            self._proc = subprocess.Popen(self._chrome_cmd, shell=False)

    @property
    def remote_port(self) -> int:
        """Remote debugging port."""
        return self._remote_port

    @wait_until_finished(timeout=5, throw_exception=False)
    def _delete_profile(self) -> bool:
        """Delete profile.

        Returns:
            `True` on successful deletion, `False` on failure.
        """
        shutil.rmtree(self._profile_path, ignore_errors=True)
        profile_deleted = not os.path.isdir(self._profile_path)
        return profile_deleted

    def close(self) -> None:
        """Close browser and delete temporary profile."""
        logger.debug('Завершение работы Chrome Браузера.')

        # Close the browser together with its child processes, so a persistent
        # profile is not left locked by leftovers (next launch would hand off to them).
        try:
            import psutil
            children = psutil.Process(self._proc.pid).children(recursive=True)
        except Exception:
            children = []
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        for child in children:
            try:
                child.kill()
            except Exception:
                pass

        # Delete temporary profile (persistent one is kept)
        if not self._persistent_profile:
            self._delete_profile()

    def __repr__(self) -> str:
        classname = self.__class__.__name__
        return f'{classname}(arguments={self._chrome_cmd!r})'

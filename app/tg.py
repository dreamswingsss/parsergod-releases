"""Telegram с аккаунта пользователя: вход по номеру и коду, очередь сообщений лидам.

Работает через официальный клиентский API Telegram (Telethon). Нужны api_id и
api_hash с my.telegram.org — их получает сам пользователь. Сессия хранится в
папке настроек программы. Сообщения уходят только тем, у кого в карточке есть
Telegram-ссылка с username, с паузами и дневным лимитом: массовые холодные
сообщения Telegram ограничивает спам-блоком.
"""
from __future__ import annotations

import asyncio
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_DAILY_LIMIT = 15
DEFAULT_DELAY = (45, 120)  # секунд между сообщениями


def username_of(link: Optional[str]) -> Optional[str]:
    """'https://t.me/barber_wolf' -> 'barber_wolf'. Ссылки-приглашения и каналы-боты без username — None."""
    for part in (link or '').split(','):
        m = re.search(r'(?:t\.me|telegram\.me)/(?!\+|joinchat/|c/|s/)@?([A-Za-z][\w]{3,31})(?:[/?#]|$)', part.strip())
        if m:
            return m.group(1)
    return None


class TelegramSender:
    def __init__(self, session_dir: Path, on_sent: Callable[[str, bool, str], None],
                 make_text: Callable[[str], str]) -> None:
        self.session_path = str(Path(session_dir) / 'telegram')
        self.on_sent = on_sent        # (lead_id, ok, error_text)
        self.make_text = make_text    # lead_id -> текст сообщения (шаблон или ИИ), считается перед отправкой
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client = None
        self._api: Optional[tuple] = None
        self._phone = ''
        self._phone_hash = ''
        self.me = ''
        self.queue: List[Dict[str, Any]] = []
        self.log: List[Dict[str, str]] = []
        self.sending = False
        self.next_at = 0.0
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None

    # ---------- служебное ----------
    def _run(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def _client_for(self, api_id: int, api_hash: str):
        from telethon import TelegramClient
        if self._client is None or self._api != (api_id, api_hash):
            if self._client is not None:
                try:
                    self._run(self._client.disconnect(), 10)
                except Exception:
                    pass

            async def make():
                return TelegramClient(self.session_path, api_id, api_hash, connection_retries=2, retry_delay=2,
                                      timeout=15, device_model='ParserGod', system_version='Desktop',
                                      app_version='1.1')
            self._client = self._run(make())
            self._api = (api_id, api_hash)
        return self._client

    def _log(self, text: str, level: str = 'info') -> None:
        self.log.append({'t': time.strftime('%H:%M'), 'text': text, 'level': level})
        del self.log[:-200]

    # ---------- вход ----------
    def restore(self, api_id: int, api_hash: str) -> str:
        """Подключается к сохранённой сессии; возвращает имя аккаунта или ''."""
        if not api_id or not api_hash:
            return ''
        c = self._client_for(api_id, api_hash)

        async def go():
            if not c.is_connected():
                await c.connect()
            if await c.is_user_authorized():
                me = await c.get_me()
                return '@' + me.username if me.username else (me.first_name or 'аккаунт')
            return ''
        try:
            self.me = self._run(go(), 30)
        except Exception:
            self.me = ''
        return self.me

    def send_code(self, api_id: int, api_hash: str, phone: str) -> None:
        c = self._client_for(api_id, api_hash)
        phone = re.sub(r'[^\d+]', '', phone or '')

        async def go():
            if not c.is_connected():
                await c.connect()
            sent = await c.send_code_request(phone)
            return sent.phone_code_hash
        self._phone = phone
        self._phone_hash = self._run(go())

    def sign_in(self, code: str = '', password: str = '') -> Dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError
        c = self._client
        if c is None:
            raise RuntimeError('Сначала запросите код')

        async def go():
            if password:
                await c.sign_in(password=password)
            else:
                await c.sign_in(phone=self._phone, code=re.sub(r'\D', '', code), phone_code_hash=self._phone_hash)
            me = await c.get_me()
            return '@' + me.username if me.username else (me.first_name or 'аккаунт')
        try:
            self.me = self._run(go())
        except SessionPasswordNeededError:
            return {'ok': False, 'need_password': True}
        return {'ok': True, 'account': self.me}

    def logout(self) -> None:
        self.stop()
        if self._client is not None:
            try:
                self._run(self._client.log_out(), 20)
            except Exception:
                pass
        self.me = ''

    # ---------- отправка ----------
    def enqueue(self, items: List[Dict[str, Any]]) -> int:
        """items: [{'id', 'name', 'username'}]. Дубликаты в очереди пропускаются."""
        have = {q['id'] for q in self.queue}
        n = 0
        for it in items:
            if it['id'] not in have and it.get('username'):
                self.queue.append(it)
                have.add(it['id'])
                n += 1
        return n

    def start(self, sent_today: Callable[[], int], daily_limit: int, delay: tuple = DEFAULT_DELAY) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._work, args=(sent_today, daily_limit, delay), daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self.sending = False

    def _work(self, sent_today: Callable[[], int], daily_limit: int, delay: tuple) -> None:
        from telethon import errors
        self.sending = True
        first = True
        try:
            while self.queue and not self._stop.is_set():
                if sent_today() >= daily_limit:
                    self._log('Дневной лимит %d сообщений исчерпан — продолжу завтра' % daily_limit, 'warn')
                    break
                if not first:
                    wait = random.uniform(*delay)
                    self.next_at = time.time() + wait
                    if self._stop.wait(wait):
                        break
                first = False
                item = self.queue.pop(0)
                self.next_at = 0
                try:
                    text = self.make_text(item['id'])
                except Exception as e:
                    self._log('Нет текста для %s: %s' % (item['name'], e), 'error')
                    self.on_sent(item['id'], False, 'нет текста')
                    continue
                if not text:
                    self._log('Пустой текст для %s — пропущено' % item['name'], 'error')
                    continue
                try:
                    self._run(self._client.send_message(item['username'], text, link_preview=True), 60)
                    self._log('Отправлено: %s (@%s)' % (item['name'], item['username']), 'ok')
                    self.on_sent(item['id'], True, '')
                except errors.FloodWaitError as e:
                    self.queue.insert(0, item)
                    self._log('Telegram просит паузу %d мин — жду' % max(1, e.seconds // 60), 'warn')
                    if self._stop.wait(e.seconds + 5):
                        break
                except errors.PeerFloodError:
                    self.queue.insert(0, item)
                    self._log('Telegram ограничил аккаунт за рассылку (спам-блок). Отправка остановлена. '
                              'Проверьте @SpamBot и сделайте перерыв.', 'error')
                    break
                except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError):
                    self._log('Нет такого пользователя: @%s (%s)' % (item['username'], item['name']), 'error')
                    self.on_sent(item['id'], False, 'нет такого username')
                except errors.UserPrivacyRestrictedError:
                    self._log('%s закрыл сообщения от незнакомых' % item['name'], 'error')
                    self.on_sent(item['id'], False, 'закрыты сообщения')
                except Exception as e:
                    self._log('Не отправлено %s: %s' % (item['name'], e.__class__.__name__), 'error')
                    self.on_sent(item['id'], False, e.__class__.__name__)
        finally:
            self.sending = False
            self.next_at = 0

    def status(self) -> Dict[str, Any]:
        return {'account': self.me, 'queue': [{'id': q['id'], 'name': q['name']} for q in self.queue],
                'sending': self.sending, 'next_in': max(0, int(self.next_at - time.time())) if self.next_at else 0,
                'log': self.log[-30:]}

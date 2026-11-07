"""ИИ по ключу пользователя: генерация демо-сайтов и сообщений.

Ключ хранится только в настройках на компьютере пользователя. Поддерживаются:
- YandexGPT (Yandex Cloud: API-ключ + ID каталога) — работает в России, оплата в рублях;
- OpenRouter — один ключ на Gemini, Claude, DeepSeek, GPT и другие модели;
- Claude API (Anthropic);
- любой OpenAI-совместимый сервис (DeepSeek, OpenAI и т.п.): адрес + ключ + модель.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

PROVIDERS = {
    'yandex': {'title': 'YandexGPT (Yandex Cloud)', 'model': 'yandexgpt/latest',
               'help': 'https://yandex.cloud/ru/docs/foundation-models/quickstart/yandexgpt',
               'needs': ['key', 'folder']},
    'openrouter': {'title': 'OpenRouter (Gemini, Claude, DeepSeek…)', 'model': 'google/gemini-2.5-flash',
                   'help': 'https://openrouter.ai/keys', 'needs': ['key', 'model']},
    'anthropic': {'title': 'Claude API (Anthropic)', 'model': 'claude-sonnet-5-5',
                  'help': 'https://console.anthropic.com/settings/keys', 'needs': ['key', 'model']},
    'openai': {'title': 'OpenAI-совместимый (DeepSeek, OpenAI…)', 'model': 'deepseek-chat',
               'base': 'https://api.deepseek.com/v1', 'help': '', 'needs': ['key', 'model', 'base']},
}


class AIError(Exception):
    pass


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: float = 240) -> Dict[str, Any]:
    import requests
    try:
        r = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.Timeout:
        raise AIError('ИИ долго не отвечает. Попробуйте ещё раз.')
    except requests.RequestException as e:
        raise AIError('Нет связи с сервисом ИИ (%s). Возможно, он недоступен из вашей страны без VPN.'
                      % e.__class__.__name__)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code in (401, 403):
        raise AIError('Сервис ИИ не принял ключ. Проверьте ключ в настройках.')
    if r.status_code == 402:
        raise AIError('На балансе сервиса ИИ закончились деньги.')
    if r.status_code == 429:
        raise AIError('Сервис ИИ просит подождать (слишком много запросов). Попробуйте через минуту.')
    if r.status_code >= 400:
        err = data.get('error')
        msg = err.get('message') if isinstance(err, dict) else (err if isinstance(err, str) else data.get('message'))
        if r.status_code == 400 and 'location' in str(msg).lower():
            raise AIError('Этот сервис ИИ не работает в вашей стране. Выберите YandexGPT или OpenRouter.')
        raise AIError('Сервис ИИ: %s' % (msg or 'ошибка %d' % r.status_code))
    return data


def complete(cfg: Dict[str, Any], prompt: str, system: str = '', max_tokens: int = 8000,
             timeout: float = 240) -> str:
    """Один запрос к модели. cfg = {'provider', 'key', 'model', 'folder', 'base'}."""
    provider = cfg.get('provider') or ''
    key = (cfg.get('key') or '').strip()
    if provider not in PROVIDERS:
        raise AIError('ИИ не подключён. Настройки → ИИ.')
    if not key:
        raise AIError('Не указан ключ ИИ.')
    model = (cfg.get('model') or '').strip() or PROVIDERS[provider]['model']

    if provider == 'yandex':
        folder = (cfg.get('folder') or '').strip()
        if not folder:
            raise AIError('Для YandexGPT нужен ID каталога Yandex Cloud.')
        uri = model if model.startswith('gpt://') else 'gpt://%s/%s' % (folder, model)
        msgs = ([{'role': 'system', 'text': system}] if system else []) + [{'role': 'user', 'text': prompt}]
        data = _post('https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
                     {'Authorization': 'Api-Key ' + key, 'x-folder-id': folder},
                     {'modelUri': uri, 'completionOptions': {'stream': False, 'temperature': 0.6,
                                                             'maxTokens': str(max_tokens)},
                      'messages': msgs}, timeout)
        try:
            return data['result']['alternatives'][0]['message']['text']
        except (KeyError, IndexError, TypeError):
            raise AIError('YandexGPT вернул пустой ответ.')

    if provider == 'anthropic':
        body: Dict[str, Any] = {'model': model, 'max_tokens': max_tokens,
                                'messages': [{'role': 'user', 'content': prompt}]}
        if system:
            body['system'] = system
        data = _post('https://api.anthropic.com/v1/messages',
                     {'x-api-key': key, 'anthropic-version': '2023-06-01'}, body, timeout)
        parts = [b.get('text', '') for b in data.get('content') or [] if b.get('type') == 'text']
        if not parts:
            raise AIError('Claude вернул пустой ответ.')
        return ''.join(parts)

    base = 'https://openrouter.ai/api/v1' if provider == 'openrouter' else \
        (cfg.get('base') or PROVIDERS['openai']['base']).rstrip('/')
    msgs2 = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': prompt}]
    headers = {'Authorization': 'Bearer ' + key}
    if provider == 'openrouter':
        headers.update({'HTTP-Referer': 'https://parsergod.app', 'X-Title': 'ParserGod'})
    data = _post(base + '/chat/completions', headers,
                 {'model': model, 'messages': msgs2, 'max_tokens': max_tokens, 'temperature': 0.6}, timeout)
    try:
        return data['choices'][0]['message']['content'] or ''
    except (KeyError, IndexError, TypeError):
        raise AIError('Сервис ИИ вернул пустой ответ.')


def check(cfg: Dict[str, Any]) -> str:
    """Короткий пробный запрос — проверка ключа."""
    out = complete(cfg, 'Ответь одним словом: работает', max_tokens=20, timeout=60)
    return out.strip()[:40] or 'ok'


# ---------- сообщения ----------

MESSAGE_SYSTEM = ('Ты пишешь первые сообщения владельцам местного бизнеса от имени фрилансера, который делает сайты. '
                  'Пишешь как живой человек в мессенджере: коротко, на «вы», без восклицательных знаков, '
                  'без рекламных штампов, без смайликов, ничего не выдумываешь.')


def message_prompt(lead: Dict[str, Any], offer: str = '') -> str:
    facts = {k: lead.get(k) for k in ('name', 'category', 'city', 'rating', 'reviews', 'site_kind', 'website')
             if lead.get(k) not in (None, '')}
    demo = lead.get('demo_url') or ''
    return ('Напиши одно первое сообщение этой организации в WhatsApp/Telegram, 2–4 предложения.\n'
            'Данные из 2ГИС: %s\n'
            '%s'
            'Что я предлагаю: %s\n'
            'Опирайся на конкретику (рейтинг, сфера, нет сайта / только соцсети / сайт на конструкторе). '
            'Закончи простым вопросом. Ответь только текстом сообщения, без кавычек и пояснений.'
            % (facts, ('Ссылка на демо-сайт, который я уже сделал для них (вставь её в сообщение как есть): %s\n' % demo)
               if demo else '', offer.strip() or 'сделать сайт с онлайн-записью и контактами за несколько дней'))


def clean_message(text: str) -> str:
    t = (text or '').strip()
    t = re.sub(r'^```\w*\s*|\s*```$', '', t).strip()
    if len(t) > 2 and t[0] in '"«“' and t[-1] in '"»”':
        t = t[1:-1].strip()
    return t[:1500]


def public_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Настройки ИИ для окна — без самого ключа."""
    cfg = cfg or {}
    key = cfg.get('key') or ''
    return {'provider': cfg.get('provider') or '', 'model': cfg.get('model') or '', 'folder': cfg.get('folder') or '',
            'base': cfg.get('base') or '', 'connected': bool(cfg.get('provider') and key),
            'key_hint': ('…' + key[-4:]) if len(key) > 8 else '', 'offer': cfg.get('offer') or ''}

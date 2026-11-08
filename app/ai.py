"""ИИ по ключу пользователя: генерация демо-сайтов и сообщений.

Ключ хранится только в настройках на компьютере пользователя. Поддерживаются:
- YandexGPT (Yandex Cloud: API-ключ + ID каталога) — работает в России, оплата в рублях;
- OpenRouter — один ключ на Gemini, Claude, DeepSeek, GPT и другие модели;
- Claude API (Anthropic);
- любой OpenAI-совместимый сервис (DeepSeek, OpenAI и т.п.): адрес + ключ + модель.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# kind: как с ним говорить; base: адрес OpenAI-совместимого API; prefer: какие модели предлагать первыми.
PROVIDERS = {
    'openrouter': {'title': 'OpenRouter', 'kind': 'openai', 'base': 'https://openrouter.ai/api/v1',
                   'model': 'google/gemini-2.5-flash', 'help': 'https://openrouter.ai/keys',
                   'prefer': ['google/gemini-2.5-flash', 'anthropic/claude-sonnet', 'deepseek/deepseek-chat',
                              'openai/gpt-5-mini', 'google/gemini-2.5-pro', 'anthropic/claude']},
    'anthropic': {'title': 'Claude API (Anthropic)', 'kind': 'anthropic', 'base': 'https://api.anthropic.com/v1',
                  'model': 'claude-sonnet-5-5', 'help': 'https://console.anthropic.com/settings/keys',
                  'prefer': ['claude-sonnet', 'claude-haiku', 'claude-opus']},
    'openai': {'title': 'OpenAI (ChatGPT)', 'kind': 'openai', 'base': 'https://api.openai.com/v1',
               'model': 'gpt-5-mini', 'help': 'https://platform.openai.com/api-keys',
               'prefer': ['gpt-5-mini', 'gpt-5', 'gpt-4.1', 'gpt-4o']},
    'deepseek': {'title': 'DeepSeek', 'kind': 'openai', 'base': 'https://api.deepseek.com/v1',
                 'model': 'deepseek-chat', 'help': 'https://platform.deepseek.com/api_keys',
                 'prefer': ['deepseek-chat', 'deepseek-reasoner']},
    'gemini': {'title': 'Google Gemini', 'kind': 'openai', 'base': 'https://generativelanguage.googleapis.com/v1beta/openai',
               'model': 'gemini-2.5-flash', 'help': 'https://aistudio.google.com/apikey',
               'prefer': ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash']},
    'groq': {'title': 'Groq', 'kind': 'openai', 'base': 'https://api.groq.com/openai/v1',
             'model': 'llama-3.3-70b-versatile', 'help': 'https://console.groq.com/keys',
             'prefer': ['llama-3.3-70b', 'openai/gpt-oss-120b', 'qwen']},
    'xai': {'title': 'xAI Grok', 'kind': 'openai', 'base': 'https://api.x.ai/v1', 'model': 'grok-4',
            'help': 'https://console.x.ai', 'prefer': ['grok-4', 'grok-3']},
    'yandex': {'title': 'YandexGPT (Yandex Cloud)', 'kind': 'yandex', 'base': '', 'model': 'yandexgpt/latest',
               'help': 'https://yandex.cloud/ru/docs/foundation-models/quickstart/yandexgpt',
               'prefer': ['yandexgpt/latest', 'yandexgpt-lite/latest']},
    'custom': {'title': 'Другой OpenAI-совместимый', 'kind': 'openai', 'base': '', 'model': '', 'help': '',
               'prefer': []},
}
NOT_IN_RUSSIA = {'anthropic', 'openai', 'gemini'}  # без VPN из России не работают


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
        if 'location' in str(msg).lower() or 'region' in str(msg).lower() or 'country' in str(msg).lower():
            raise AIError('Этот сервис ИИ не работает в вашей стране без VPN. Подойдут OpenRouter, DeepSeek или YandexGPT.')
        raise AIError('Сервис ИИ: %s' % (msg or 'ошибка %d' % r.status_code))
    return data


def complete(cfg: Dict[str, Any], prompt: str, system: str = '', max_tokens: int = 8000,
             timeout: float = 240) -> str:
    """Один запрос к модели. cfg = {'provider', 'key', 'model', 'folder', 'base'}."""
    provider = cfg.get('provider') or ''
    key = (cfg.get('key') or '').strip()
    if provider not in PROVIDERS:
        raise AIError('ИИ не подключён. Настройки → Подключения → ИИ.')
    kind = PROVIDERS[provider]['kind']
    if not key:
        raise AIError('Не указан ключ ИИ.')
    model = (cfg.get('model') or '').strip() or PROVIDERS[provider]['model']

    if kind == 'yandex':
        folder = (cfg.get('folder') or '').strip()
        if not folder:
            raise AIError('Для YandexGPT нужен ID каталога Yandex Cloud.')
        uri = model if model.startswith('gpt://') else 'gpt://%s/%s' % (folder, model)
        msgs = ([{'role': 'system', 'text': system}] if system else []) + [{'role': 'user', 'text': prompt}]
        data = _post('https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
                     {'Authorization': ('Bearer ' if key.startswith('t1.') else 'Api-Key ') + key, 'x-folder-id': folder},
                     {'modelUri': uri, 'completionOptions': {'stream': False, 'temperature': 0.6,
                                                             'maxTokens': str(max_tokens)},
                      'messages': msgs}, timeout)
        try:
            return data['result']['alternatives'][0]['message']['text']
        except (KeyError, IndexError, TypeError):
            raise AIError('YandexGPT вернул пустой ответ.')

    if kind == 'anthropic':
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

    base = (cfg.get('base') or PROVIDERS[provider]['base']).rstrip('/')
    if not base:
        raise AIError('Укажите адрес API сервиса.')
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


# ---------- автоопределение ключа ----------

def guess(key: str) -> List[str]:
    """Кандидаты-провайдеры по виду ключа, в порядке вероятности."""
    k = (key or '').strip()
    if k.startswith('sk-or-'):
        return ['openrouter']
    if k.startswith('sk-ant-'):
        return ['anthropic']
    if k.startswith(('sk-proj-', 'sk-svcacct-', 'sk-admin-')):
        return ['openai']
    if k.startswith('AIza'):
        return ['gemini']
    if k.startswith('gsk_'):
        return ['groq']
    if k.startswith('xai-'):
        return ['xai']
    if k.startswith(('AQVN', 'AQVO', 'AQVM', 'AQVP')):
        return ['yandex']
    if k.startswith('t1.'):
        return ['yandex']
    if re.fullmatch(r'sk-[0-9a-f]{32}', k):
        return ['deepseek', 'openai']
    if k.startswith('sk-'):
        return ['openai', 'deepseek']
    return []


def list_models(provider: str, key: str, base: str = '') -> List[str]:
    """Модели, доступные по ключу (рекомендуемые первыми). Ошибка доступа -> AIError."""
    import requests
    p = PROVIDERS[provider]
    if p['kind'] == 'yandex':
        return list(p['prefer'])
    if p['kind'] == 'anthropic':
        url, headers = p['base'] + '/models?limit=100', {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    else:
        url, headers = (base or p['base']).rstrip('/') + '/models', {'Authorization': 'Bearer ' + key}
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise AIError('Нет связи с %s (%s)' % (p['title'], e.__class__.__name__))
    if r.status_code in (401, 403):
        raise AIError('%s не принял ключ' % p['title'])
    if r.status_code >= 400:
        raise AIError('%s: ошибка %d' % (p['title'], r.status_code))
    try:
        data = r.json()
    except ValueError:
        data = {}
    ids = [str(m.get('id')) for m in (data.get('data') or data.get('models') or []) if isinstance(m, dict) and m.get('id')]
    ids = [i.replace('models/', '') for i in ids]
    if provider == 'openrouter' and ids:  # проверяем, что ключ рабочий: список моделей там публичный
        k = requests.get(p['base'] + '/key', headers=headers, timeout=20)
        if k.status_code in (401, 403):
            raise AIError('OpenRouter не принял ключ')
    pref = []
    for want in p['prefer']:
        pref += [i for i in ids if i.startswith(want) and i not in pref]
    rest = sorted(i for i in ids if i not in pref and not re.search(r'(embed|whisper|tts|dall-e|image|audio|moderation|vision-preview)', i))
    return (pref + rest)[:200] or ([p['model']] if p['model'] else [])


def detect(key: str, base: str = '') -> Dict[str, Any]:
    """Определяет сервис по ключу и подтягивает список моделей."""
    key = (key or '').strip()
    if not key:
        raise AIError('Вставьте ключ')
    cands = ['custom'] if base else guess(key)
    if not cands:
        raise AIError('Не узнал этот ключ. Подходят ключи OpenRouter (sk-or-…), Claude (sk-ant-…), OpenAI, DeepSeek, '
                      'Gemini (AIza…), Groq (gsk_…), xAI, YandexGPT (AQVN…). Для другого сервиса укажите адрес API.')
    last: Optional[Exception] = None
    for prov in cands:
        try:
            models = list_models(prov, key, base)
        except AIError as e:
            last = e
            continue
        p = PROVIDERS[prov]
        default = p['model'] if p['model'] in models else (models[0] if models else p['model'])
        return {'provider': prov, 'title': p['title'], 'models': models, 'model': default,
                'needs_folder': p['kind'] == 'yandex', 'not_in_russia': prov in NOT_IN_RUSSIA}
    raise AIError(str(last) if last else 'Не удалось проверить ключ')


def check(cfg: Dict[str, Any]) -> str:
    """Короткий пробный запрос — проверка ключа."""
    out = complete(cfg, 'Ответь одним словом: работает', max_tokens=20, timeout=60)
    return out.strip()[:40] or 'ok'


# ---------- сообщения ----------

MESSAGE_SYSTEM = ('Ты пишешь первые сообщения владельцам местного бизнеса от имени фрилансера, который делает сайты. '
                  'Пишешь как живой человек в мессенджере: коротко, на «вы», без восклицательных знаков, '
                  'без рекламных штампов, без смайликов, ничего не выдумываешь.')


def message_prompt(lead: Dict[str, Any], offer: str = '', base: str = '', wishes: str = '') -> str:
    facts = {k: lead.get(k) for k in ('name', 'category', 'city', 'rating', 'reviews', 'site_kind', 'website')
             if lead.get(k) not in (None, '')}
    demo = lead.get('demo_url') or ''
    return ('Напиши одно первое сообщение этой организации в WhatsApp/Telegram, 2–4 предложения.\n'
            'Данные из 2ГИС: %s\n'
            '%s'
            'Что я предлагаю: %s\n'
            'Опирайся на конкретику (рейтинг, сфера, нет сайта / только соцсети / сайт на конструкторе). '
            'Закончи простым вопросом.%s%s Ответь только текстом сообщения, без кавычек и пояснений.'
            % (facts, ('Ссылка на демо-сайт, который я уже сделал для них (вставь её в сообщение как есть): %s\n' % demo)
               if demo else '', offer.strip() or 'сделать сайт с онлайн-записью и контактами за несколько дней',
               ('\nВозьми за основу смысл и тон этого шаблона, но перепиши живо и под эту организацию:\n«%s»\n' % base.strip()[:1500])
               if base.strip() else '',
               ('\nПожелания к тексту: %s.' % wishes.strip()[:300]) if wishes.strip() else ''))


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
    prov = cfg.get('provider') or ''
    return {'provider': prov, 'title': PROVIDERS.get(prov, {}).get('title', ''), 'model': cfg.get('model') or '',
            'folder': cfg.get('folder') or '', 'base': cfg.get('base') or '', 'connected': bool(prov and key),
            'key_hint': ('…' + key[-4:]) if len(key) > 8 else '', 'offer': cfg.get('offer') or ''}

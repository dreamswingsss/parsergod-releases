"""Company lookups and normalized evidence for a lead's registry record."""
from __future__ import annotations

import re

from .audit import _valid_inn, checked_at


class RegistryError(ValueError):
    pass


def registration_number(value: str) -> tuple[str, str]:
    digits = re.sub(r'\s+', '', value or '')
    if not re.fullmatch(r'[0-9]{10,15}', digits):
        raise RegistryError('Введите ИНН или ОГРН цифрами.')
    if len(digits) in (10, 12) and _valid_inn(digits):
        return 'inn', digits
    if len(digits) in (13, 15):
        divisor = 11 if len(digits) == 13 else 13
        if int(digits[:-1]) % divisor % 10 == int(digits[-1]):
            return 'ogrn', digits
    raise RegistryError('Проверьте контрольную цифру ИНН или ОГРН.')


def _text(value, limit: int) -> str:
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _record(item: dict) -> dict:
    data = item.get('data') or {}
    management = data.get('management') or {}
    address = data.get('address') or {}
    state = data.get('state') or {}
    name = data.get('name') or {}
    return {
        'name': _text(name.get('full_with_opf') or item.get('value'), 300),
        'inn': _text(data.get('inn'), 12),
        'ogrn': _text(data.get('ogrn'), 15),
        'kpp': _text(data.get('kpp'), 9),
        'address': _text(address.get('value'), 300),
        'status': _text(state.get('status'), 32),
        'branch_type': _text(data.get('branch_type'), 20),
        'manager': _text(management.get('name'), 180),
        'position': _text(management.get('post'), 100),
        'source': 'DaData',
        'checked_at': checked_at(),
    }


def lookup(key: str, query: str, city: str = '', exact: bool = False, kpp: str = '') -> list[dict]:
    from dadata import Dadata
    import httpx

    query = _text(query, 240)
    if not query:
        raise RegistryError('Введите ИНН, ОГРН или название компании.')
    if exact or re.fullmatch(r'[\d\s]+', query):
        kind, query = registration_number(query)
        if kind == 'inn' and len(query) == 10 and re.fullmatch(r'[0-9]{9}', kpp):
            query += '/' + kpp
        exact = True
    try:
        with Dadata(key, timeout=12) as client:
            if exact:
                results = client.find_by_id('party', query, count=5)
            else:
                opts = {'locations_boost': [{'city': city.strip()}]} if city.strip() else {}
                results = client.suggest('party', query, count=5, **opts)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise RegistryError('DaData не приняла ключ. Проверьте ключ в настройках.') from exc
        if code == 429:
            raise RegistryError('Лимит запросов DaData исчерпан. Можно сверить реквизиты в ФНС.') from exc
        raise RegistryError(f'DaData вернула ошибку HTTP {code}.') from exc
    except httpx.RequestError as exc:
        raise RegistryError('DaData недоступна. Повторите поиск или откройте ФНС.') from exc
    return [_record(item) for item in results[:5] if isinstance(item, dict)]


def manual_record(fields: dict) -> dict:
    kind, value = registration_number(str(fields.get('number') or ''))
    name = _text(fields.get('name'), 300)
    if not name:
        raise RegistryError('Введите название из найденной записи ФНС.')
    additional = str(fields.get('other_number') or '').strip()
    numbers = {kind: value}
    if additional:
        other_kind, other_value = registration_number(additional)
        if other_kind == kind:
            raise RegistryError('Укажите по одному ИНН и ОГРН.')
        numbers[other_kind] = other_value
    return {
        **numbers,
        'name': name,
        'address': _text(fields.get('address'), 300),
        'manager': _text(fields.get('manager'), 180),
        'position': _text(fields.get('position'), 100),
        'source': 'ФНС, внесено вручную',
        'checked_at': checked_at(),
    }

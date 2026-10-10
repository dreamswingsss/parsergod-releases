"""Генерирует app/ui/localities.json — города РФ, которых нет среди регионов 2ГИС.

Для каждого города: n — название, r — область, c — регион(ы) 2ГИС, в границы которых он входит.
c = [] — город не покрыт 2ГИС (организаций там в 2ГИС нет).

Входные данные:
  city.csv        — https://github.com/hflabs/city (координаты городов РФ)
  region_map.json — {индекс строки city.csv: [коды регионов]}; считается в браузере на data.2gis.com:
                    границы регионов из catalog.api.2gis.ru/2.0/region/list (fields=items.bounds) +
                    проверка «точка в многоугольнике» для координат каждого города.
  cities.json     — список регионов 2ГИС (parser_2gis/data/cities.json)
Запуск: python gen_localities.py city.csv region_map.json cities.json localities.json
"""
import csv, json, sys

city_csv, region_map, cities_json, out = sys.argv[1:5]
rows = list(csv.DictReader(open(city_csv, encoding='utf-8')))
rmap = json.load(open(region_map, encoding='utf-8'))
regions = [c for c in json.load(open(cities_json, encoding='utf-8')) if c['country_code'] == 'ru']

def norm(s): return s.lower().replace('ё', 'е').strip()

region_names = {norm(c['name']) for c in regions}
codes = {c['code'] for c in regions}
result, seen = [], set()
for i, r in enumerate(rows):
    name = r['city'] or (r['region'] if r['region'] == 'Севастополь' else '')
    if not name or norm(name) in region_names:
        continue
    key = (name, r['address'].split(',')[0].strip())
    if key in seen:
        continue
    seen.add(key)
    result.append({'n': name, 'r': key[1], 'c': [c for c in rmap.get(str(i), []) if c in codes],
                   'p': int(r['population'] or 0)})
result.sort(key=lambda x: -x['p'])
for x in result:
    del x['p']
json.dump(result, open(out, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print(len(result), 'localities,', sum(1 for x in result if x['c']), 'covered by 2GIS')

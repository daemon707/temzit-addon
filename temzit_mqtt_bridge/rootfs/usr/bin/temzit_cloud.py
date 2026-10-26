#!/usr/bin/env python3
"""
Temzit cloud client — чтение и запись настроек/расписания через облако.

Ходит на тот же API, что и официальное веб-приложение service.temzit.ru/app (см. docs/app.js):
    GET  https://service.temzit.ru/api/GetData?cmd=N&login=..&serial=..&pass=..
      cmd=2 -> HTML-фрагмент формы настроек (поля P1, P15, TAmode, ...)
      cmd=4 -> HTML-фрагмент формы расписания (4 строки, поля P0..P76)
    POST тот же URL, multipart/form-data со ВСЕМИ полями формы + submit=true:
      cmd=3 -> сохранить настройки, cmd=5 -> сохранить расписание
Запись повторяет поведение браузера: берём свежую форму, меняем только разрешённые поля, остальные
отправляем ровно в том виде, в каком получили. Гидромодуль забирает изменения с сервера сам
(раз в «период обращения к серверу»), поэтому применение занимает до пары минут.

Только стандартная библиотека. Пароль никогда не попадает в логи и тексты ошибок.
"""
import uuid
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

CLOUD_URL = 'https://service.temzit.ru/api/GetData'
HTTP_ERRORS = {401: 'ошибка авторизации', 402: 'неправильный серийный номер', 403: 'неправильный серийный номер',
               404: 'нет данных', 406: 'нет доступа', 500: 'ошибка сервера'}

# Поля, без которых ответ считаем неполным (защита от пустой/обрезанной страницы).
REQUIRED_CFG = ('P1', 'P2', 'P3', 'P15', 'P16', 'P8', 'P9', 'P88')
SCHEDULE_BASES = (0, 20, 40, 60)
# Смещения полей внутри строки расписания: имя поля = 'P' + (база + смещение).
SCHEDULE_FIELDS = (('mode', 0), ('start', 2), ('end', 4), ('room', 6), ('water', 8),
                   ('dhw_target', 10), ('compressor_limit', 12), ('heater', 14), ('dhw_mode', 16))
SCHEDULE_OFFSETS = dict(SCHEDULE_FIELDS)

# Поля настроек, которые разрешено менять из HA. Все остальные (типы ККБ, датчик протока, гликоль,
# связь, «учитывать Тул», режим ТА, солнечный коллектор) пересылаются как есть и не меняются.
# Ядро (режим, уставки, ТЭН, ГВС, лимит ККБ) меняется локально по порту 333.
WRITABLE_CFG = ('P15', 'P16', 'P10', 'P11b0', 'P11b2')


class CloudError(Exception):
    pass


class FormParser(HTMLParser):
    """Разбирает select / input из HTML-фрагмента в {имя: {'type', 'value', 'options'}}.
    Значения берутся из атрибутов (value/selected/checked), а не из подписей: сервер иногда
    отдаёт мусор в тексте опции. select без selected -> первая опция (как делает браузер)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields = {}
        self._sel = None
        self._opt = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'select' and a.get('name'):
            self._sel = {'type': 'select', 'value': None, 'options': {}}
            self.fields[a['name']] = self._sel
        elif tag == 'option' and self._sel is not None:
            self._opt = a.get('value', '')
            self._sel['options'][self._opt] = ''
            if 'selected' in a:
                self._sel['value'] = self._opt
        elif tag == 'input' and a.get('name'):
            kind = (a.get('type') or 'text').lower()
            if kind == 'checkbox':
                self.fields[a['name']] = {'type': 'checkbox', 'value': '1' if 'checked' in a else '0',
                                          'options': {'0': 'Нет', '1': 'Да'}}
            elif kind not in ('submit', 'button'):
                self.fields[a['name']] = {'type': kind, 'value': a.get('value', ''), 'options': {}}

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._sel is not None and self._opt is not None:
            self._sel['options'][self._opt] += data

    def handle_endtag(self, tag):
        if tag == 'option':
            self._opt = None
        elif tag == 'select' and self._sel is not None:
            opts = self._sel['options']
            for v in opts:
                label = opts[v].strip()
                opts[v] = v if (not label or '<' in label) else label
            if self._sel['value'] is None and opts:
                self._sel['value'] = next(iter(opts))
            self._sel = None
            self._opt = None


def parse_form(html: str) -> dict:
    p = FormParser()
    p.feed(html)
    p.close()
    return p.fields


def label_of(field: dict) -> str:
    """Человекочитаемое значение поля (подпись выбранной опции либо само значение)."""
    return field['options'].get(field['value'], field['value'])


def _request(cmd, login, serial, password, timeout, data=None, headers=None) -> str:
    query = urllib.parse.urlencode({'cmd': cmd, 'login': login, 'serial': serial, 'pass': password})
    req = urllib.request.Request(f'{CLOUD_URL}?{query}', data=data, headers=headers or {},
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise CloudError(f'HTTP {e.code}: {HTTP_ERRORS.get(e.code, "ошибка связи")}') from None
    except urllib.error.URLError as e:
        raise CloudError(f'нет связи с сервером: {e.reason}') from None
    except Exception as e:
        raise CloudError(f'ошибка запроса: {type(e).__name__}') from None


def fetch(cmd: int, login: str, serial: str, password: str, timeout: int = 20) -> str:
    return _request(cmd, login, serial, password, timeout)


def form_items(fields: dict) -> list:
    """Пары (имя, значение) так, как форму отправил бы браузер (FormData в app.js):
    select/number — текущее значение, checkbox — только если отмечен; плюс submit=true."""
    items = []
    for name, f in fields.items():
        if f['type'] == 'checkbox':
            if f['value'] == '1':
                items.append((name, '1'))
        else:
            items.append((name, f['value']))
    items.append(('submit', 'true'))
    return items


def encode_multipart(items: list):
    boundary = '----temzit' + uuid.uuid4().hex
    parts = []
    for k, v in items:
        parts += [f'--{boundary}', f'Content-Disposition: form-data; name="{k}"', '', str(v)]
    parts += [f'--{boundary}--', '']
    return '\r\n'.join(parts).encode('utf-8'), f'multipart/form-data; boundary={boundary}'


def post(cmd: int, items: list, login, serial, password, timeout=20) -> str:
    body, ctype = encode_multipart(items)
    return _request(cmd, login, serial, password, timeout, data=body, headers={'Content-Type': ctype})


def check_value(name: str, field: dict, value) -> str:
    """Значение должно быть одним из вариантов формы — ничего «нереалистичного» не уйдёт."""
    v = str(value).strip()
    if field['type'] == 'checkbox':
        if v not in ('0', '1'):
            raise CloudError(f'{name}: ожидается 0 или 1, получено {v!r}')
    elif field['type'] == 'select':
        if v not in field['options']:
            raise CloudError(f'{name}: недопустимое значение {v!r}')
    else:
        raise CloudError(f'{name}: поле типа {field["type"]} из HA не меняется')
    return v


def _apply(fields: dict, changes: dict) -> dict:
    applied = {}
    for name, value in changes.items():
        f = fields.get(name)
        if f is None:
            raise CloudError(f'в форме нет поля {name}')
        v = check_value(name, f, value)
        if f['value'] != v:
            f['value'] = v
            applied[name] = v
    return applied


def set_config(changes: dict, login, serial, password, timeout=20, before_post=None):
    """Меняет разрешённые поля настроек. Возвращает (applied, status):
    applied — что реально изменилось;
    status — 'nochange' | 'accepted' (сервер уже отдаёт новые значения) | 'pending'.
    before_post(values) вызывается с полной формой ДО изменения прямо перед отправкой
    (для бэкапа); если он бросает исключение — ничего не отправляется."""
    bad = [n for n in changes if n not in WRITABLE_CFG]
    if bad:
        raise CloudError(f'эти поля нельзя менять из HA: {",".join(bad)}')
    fields = get_config(login, serial, password, timeout)
    before = {n: f['value'] for n, f in fields.items()}
    applied = _apply(fields, changes)
    if not applied:
        return {}, 'nochange'
    if before_post:
        before_post(before)
    post(3, form_items(fields), login, serial, password, timeout)
    after = get_config(login, serial, password, timeout)
    ok = all(after[n]['value'] == v for n, v in applied.items())
    return applied, 'accepted' if ok else 'pending'


def schedule_field(row: int, key: str) -> str:
    if row not in (1, 2, 3, 4) or key not in SCHEDULE_OFFSETS:
        raise CloudError(f'нет такого поля расписания: строка {row}, {key}')
    return f'P{SCHEDULE_BASES[row - 1] + SCHEDULE_OFFSETS[key]}'


def set_schedule(row: int, changes: dict, login, serial, password, timeout=20, before_post=None):
    """Меняет поля одной строки расписания (ключи из SCHEDULE_FIELDS). Возврат как у set_config."""
    named = {schedule_field(row, k): v for k, v in changes.items()}
    fields = parse_form(fetch(4, login, serial, password, timeout))
    for n in (schedule_field(r, k) for r in (1, 2, 3, 4) for k, _ in SCHEDULE_FIELDS):
        if n not in fields:
            raise CloudError(f'в ответе расписания нет поля {n}')
    before = {n: f['value'] for n, f in fields.items()}
    applied = _apply(fields, named)
    if not applied:
        return {}, 'nochange'
    if before_post:
        before_post(before)
    post(5, form_items(fields), login, serial, password, timeout)
    after = parse_form(fetch(4, login, serial, password, timeout))
    ok = all(after.get(n, {}).get('value') == v for n, v in applied.items())
    return applied, 'accepted' if ok else 'pending'


def get_config(login, serial, password, timeout=20) -> dict:
    fields = parse_form(fetch(2, login, serial, password, timeout))
    missing = [n for n in REQUIRED_CFG if n not in fields]
    if missing:
        raise CloudError(f'в ответе настроек нет полей: {",".join(missing)}')
    return fields


def get_schedule(login, serial, password, timeout=20, fields=None) -> list:
    """Возвращает 4 строки расписания: [{'row', 'summary', 'raw', 'labels', 'options'}, ...]."""
    if fields is None:
        fields = parse_form(fetch(4, login, serial, password, timeout))
    rows = []
    for n, base in enumerate(SCHEDULE_BASES, start=1):
        raw, labels, options = {}, {}, {}
        for key, off in SCHEDULE_FIELDS:
            f = fields.get(f'P{base + off}')
            if f is None:
                raise CloudError(f'в ответе расписания нет поля P{base + off}')
            raw[key] = f['value']
            labels[key] = label_of(f)
            options[key] = dict(f['options'])
        summary = labels['mode'] if raw['mode'] == '0' else f"{labels['mode']} {labels['start']}–{labels['end']}"
        rows.append({'row': n, 'summary': summary, 'raw': raw, 'labels': labels, 'options': options})
    return rows


# ---------------------------------------------------------------------------------------------
# Статистика (графики) — /stats/getdata под сессией сайта (API cmd=6 отдаёт HTTP 500).
# Ответ: JSON с рядами за текущие сутки по времени контроллера:
#   {"t_outdoor": [[мин, °C], ...], "t_indoor": ..., "t_water_supply": ..., "t_water_return": ...,
#    "t_freon_supply": ..., "t_freon_return": ..., "axis": [[мин, подпись], ...]}
# x — минуты от 00:00; шаг у живого getdata 1 мин, у сохранённой страницы ~5,5 мин (259 точек). Это те же данные, что страница
# service.temzit.ru/stats (там они зашиты в InitData). Ряды t_hot_water/t_hot_water2 бывают не у всех.
STATS_SERIES = ('t_outdoor', 't_indoor', 't_water_supply', 't_water_return',
                't_freon_supply', 't_freon_return', 't_hot_water', 't_hot_water2')
# Компрессор считаем работающим, когда подача теплее обратки и фреон горячий (по дампам 20–21.10.2026:
# работа — ΔT 4–6 К при фреоне 50–56 °C; простой — ΔT < 0 при фреоне ~ температуре воды).
COMP_ON_DT = 1.5
COMP_ON_FREON = 45.0


SITE_URL = 'https://service.temzit.ru'
# Вход на сайт (проверено curl 2026-10-26): сначала GET /login — сервер выдаёт куку сессии; затем POST
# /login с этой кукой (поля usrLogin, usrPassw, logmein=sign-in, CSRF нет) → 302 на /status, и сессия
# становится авторизованной. POST без предварительного GET или на корень сайта сессию НЕ даёт.
LOGIN_PATH = '/login'


def _parse_stats(text: str) -> dict:
    import json
    try:
        data = json.loads(text)
    except ValueError:
        raise CloudError('ответ графиков не JSON (сессия не принята?)') from None
    if not isinstance(data, dict) or 't_outdoor' not in data or 't_water_supply' not in data:
        raise CloudError('в ответе графиков нет рядов')
    return data


def get_stats(login, serial, password, timeout=20) -> dict:
    """Ряды графиков за текущие сутки. Через API (cmd=6 с логином в адресе) сервер отвечает
    HTTP 500 (проверено 2026-10-26), поэтому делаем как браузер: вход формой → кука сессии →
    GET /stats/getdata. serial не нужен: статистика привязана к учётной записи."""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    ua = {'User-Agent': 'Mozilla/5.0 (temzit-addon)'}
    body = urllib.parse.urlencode({'usrLogin': login, 'usrPassw': password, 'logmein': 'sign-in'}).encode()
    try:
        with opener.open(urllib.request.Request(SITE_URL + LOGIN_PATH, headers=ua), timeout=timeout) as r:
            r.read()                                   # кука сессии
        req = urllib.request.Request(SITE_URL + LOGIN_PATH, data=body, method='POST',
                                     headers={**ua, 'Content-Type': 'application/x-www-form-urlencoded',
                                              'Referer': SITE_URL + LOGIN_PATH})
        with opener.open(req, timeout=timeout) as r:   # 302 → /status; urllib проходит редирект сам
            r.read()
        req = urllib.request.Request(SITE_URL + '/stats/getdata',
                                     headers={**ua, 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
        with opener.open(req, timeout=timeout) as r:
            final = r.geturl()
            text = r.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise CloudError(f'HTTP {e.code}: {HTTP_ERRORS.get(e.code, "ошибка связи")}') from None
    except urllib.error.URLError as e:
        raise CloudError(f'нет связи с сервером: {e.reason}') from None
    except Exception as e:
        raise CloudError(f'ошибка запроса: {type(e).__name__}') from None
    if '/login' in final:
        raise CloudError('вход на сайт не принят (неверный логин/пароль?)')
    return _parse_stats(text)


def _points(day: dict, x_from: int, x_to: int, x_shift: int = 0) -> list:
    """Точки [x, out, in, sup, ret, frs] для x в [x_from, x_to) с общим сдвигом x (для сшивки суток)."""
    cols = [dict(day.get(k) or []) for k in ('t_outdoor', 't_indoor', 't_water_supply', 't_water_return', 't_freon_supply')]
    out = []
    for x in sorted(cols[0]):
        if x_from <= x < x_to:
            out.append([x + x_shift] + [c.get(x) for c in cols])
    return out


def _summarize(points: list) -> dict:
    """Сводка по точкам: доля времени работы компрессора, средние подача/обратка/ΔT при работе,
    Тдом в начале/конце и её изменение, средняя улица."""
    def mean(v):
        v = [a for a in v if a is not None]
        return round(sum(v) / len(v), 1) if v else None
    on = [p for p in points if p[3] is not None and p[4] is not None and p[5] is not None
          and (p[3] - p[4]) > COMP_ON_DT and p[5] > COMP_ON_FREON]
    ins = [p[2] for p in points if p[2] is not None]
    # Шаг между точками (мин) — у живого /stats/getdata 1 мин, у сохранённой страницы ~5,5 мин.
    step = (points[-1][0] - points[0][0]) / (len(points) - 1) if len(points) > 1 else 0
    return {
        'points': len(points),
        'step_min': round(step, 1),
        'hours': round(len(points) * step / 60, 1) if points else 0,
        'compressor_duty': round(100.0 * len(on) / len(points)) if points else None,
        'compressor_hours': round(len(on) * step / 60, 1) if points else None,
        't_supply_on': mean([p[3] for p in on]), 't_return_on': mean([p[4] for p in on]),
        'dt_on': mean([p[3] - p[4] for p in on]),
        't_indoor_start': ins[0] if ins else None, 't_indoor_end': ins[-1] if ins else None,
        't_indoor_delta': round(ins[-1] - ins[0], 1) if ins else None,
        't_indoor_min': min(ins) if ins else None, 't_indoor_max': max(ins) if ins else None,
        't_outdoor_mean': mean([p[1] for p in points]),
    }


def night_summary(prev_day, day: dict, start_h: int = 23, end_h: int = 5) -> dict:
    """Сводка ночи start_h (вчера) … end_h (сегодня): вчерашние точки с x ≥ start_h*60 плюс
    сегодняшние с x < end_h*60. Без вчерашних данных — только сегодняшняя часть (hours покажет)."""
    pts = []
    if prev_day:
        pts += _points(prev_day, start_h * 60, 1440, x_shift=-1440)
    pts += _points(day, 0, end_h * 60)
    s = _summarize(pts)
    s.update({'window': f'{start_h:02d}:00–{end_h:02d}:00', 'has_prev_day': bool(prev_day)})
    return s


def day_summary(day: dict) -> dict:
    """Сводка за сутки целиком."""
    return _summarize(_points(day, 0, 1440))

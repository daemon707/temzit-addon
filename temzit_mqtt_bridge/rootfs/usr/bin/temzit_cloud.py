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

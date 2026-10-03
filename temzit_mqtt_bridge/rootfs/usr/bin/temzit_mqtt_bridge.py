#!/usr/bin/env python3
"""
Temzit MQTT Bridge v0.12.0 (CLOUD/LOCAL MODE SWITCH)
Изменения 0.12.0: переключатель switch.temzit_cloud_mode «Облачный режим». Выключен — аддон не
обращается к облаку, облачные сущности недоступны (availability temzit/cloud/availability),
облачные команды отклоняются. Выбор хранится в data_dir/cloud_mode.json.

Изменения 0.11.2 (ENTITY ID MIGRATION):
Изменения 0.11.2: сущности ctl_* и cloud_* публикуются под unique_id ревизии _v2, а их старые
discovery-записи стираются. Так сущности, созданные HA 2026.4+ с id из русских названий,
удаляются и создаются заново с правильными entity_id — вручную ничего переименовывать не нужно.

Изменения 0.11.1 (HA 2026.4+ ENTITY IDS):
Изменения 0.11.1: в discovery вместо obj_id публикуется default_entity_id ("<домен>.temzit_...").
HA 2026.4+ больше не учитывает obj_id, и новые сущности получали id из русских названий.

Изменения 0.11.0 (CONTROL ENTITIES):
Изменения 0.11.0: сущности локального управления для карточки HA — select «Режим работы»
(полный список P1), number Тдома/Тводы/Тгвс/погодокомпенсация, select «Лимит ККБ». Пишут через
проверенный путь записи (порт 333) и работают только при write_enabled=true.

Изменения 0.10.0: запись через облако (cloud_write_enabled, по умолчанию выключено). Гистерезисы
отопления/ГВС, макс. ГВС от ТН, дезинфекция, разморозка в БКН и все поля 4 строк расписания
меняются из HA сущностями number/switch/select. Запись = POST всей формы (cmd=3/cmd=5) как в
официальном приложении; меняются только разрешённые поля, значения — только из вариантов формы;
перед отправкой — обязательный бэкап формы; изменения за 3 с объединяются в одну отправку.

Изменения 0.9.0: необязательный облачный модуль ТОЛЬКО ДЛЯ ЧТЕНИЯ (temzit_cloud.py). Если заданы
cloud_login/cloud_serial/cloud_password, аддон раз в cloud_interval секунд забирает с
service.temzit.ru настройки (cmd=2) и расписание (cmd=4) и публикует их сенсорами HA:
гистерезисы, режим ТА, параметры ККБ2, гликоль и т.д. + 4 строки расписания. Записи в облако НЕТ.

Изменения 0.8.7 (кадр записи разгадан и подтверждён на железе):
Кадр записи окончательно разгадан и подтверждён по дисплею ГМ:
    frame = [0x35, f1, config[0..29]] = 32 байта.
  - настройки (offset 0..29, ВКЛЮЧАЯ Режим) читаются из frame[2:32];
  - КС в frame[31] (= config[29]) проверяется как sum(frame[0:31]) & 0xFF;
  - f1 = (config[29] - 0x35 - sum(config[0:29])) & 0xFF — подгон, чтобы КС сошлась.
build_setcfg переписан под это; set_mode снова включён (Режим теперь пишется).
Транспорт — через nc (v0.8.6), это оказалось не критично (дело было в кадре/КС, не в транспорте).
Запись по-прежнему write_enabled=false по умолчанию — включить и проверить из HA.

История: сдвиг на байт был из-за того, что настройки клались сразу после 0x35 (а контроллер
читает с frame[2]); «ничего не пишется» — из-за неверной КС в frame[31].

История kill-switch (0.8.3): запись отключалась, т.к. кадр был неверен (0.8.1 без паддинга —
сдвиг на 1 байт; 0.8.2 с паддингом — тоже мимо). Теперь кадр верный.

Изменения 0.8.2 (попытка починки кадра записи — НЕ подтверждена):
- build_setcfg: кадр записи теперь 0x35 + 0x00(паддинг) + 30 байт + КС = 33 байта. Раньше
  паддинга не было -> контроллер писал ВСЕ параметры со сдвигом на 1 байт (подтверждено по
  дисплею ГМ). Это первый реальный тест записи на железе.
- Команда cmd/restore_raw: полное восстановление 30 байт из бэкапа одним сообщением
  (список или hex), без дедупликации, с обязательным бэкапом и валидацией.

Изменения 0.8.1: MQTT-клиент создаётся совместимо с paho-mqtt 1.x и 2.x (make_mqtt_client);
понятная диагностика при незаполненных mqtt_host/temzit_host вместо тёмной ошибки сокета.

Изменения 0.8.0 относительно 0.7.3 (по явному запросу пользователя — трогаем запись):
- ОБЯЗАТЕЛЬНЫЙ накопительный бэкап рабочего дампа ПЕРЕД каждой записью (_backup_cfg).
  Нет успешного бэкапа -> запись ОТМЕНЯЕТСЯ. Файлы пишутся в /share/temzit (доступно
  пользователю), а не в '/'. Бэкапы не перезаписываются: уникальные cfg_<ts>.json + append-журнал
  temzit_cfg_history.jsonl.
- Дедупликация: если результат совпадает с текущим конфигом — запись пропускается (бережём flash).
- Валидация РЕЗУЛЬТАТА (looks_like_valid_cfg(new_cfg)) до отправки: нереалистичные значения,
  которые ранее роняли контроллер, отклоняются и НЕ пишутся.
- Низкоуровневые build_setcfg/set_cfg (КС 1 байт, read-modify-write 30 байт) НЕ изменены.

Базовые изменения 0.7.3 относительно 0.7.1 (чтение/расшифровка и транспорт):
- Транспорт: сокет дочитывается ровно до 64 байт (раньше один recv мог вернуть
  частичный ответ -> "сдвинутый буфер" и ложные set_guard:blocked).
- Температуры в SYNC читаются как signed int16 (раньше беззнаково -> -10C давало ~6553C).
- CFG: исправлена раскладка по подтверждённому дампу + протоколу:
    * offset 3  = Инерция дома (старший ниббл) + Режим ТЭНа (младший ниббл)
    * offset 6  = Дезинфекция (старший ниббл) + Режим ГВС (младший ниббл)   <- режим ГВС жил тут, не в offset 8
    * offset 8  = Режим внешнего котла (дизель)                              <- сюда переехал бывший backup_type
    * offset 19 = Тколлектор выкл (старший ниббл) + вкл (младший ниббл)
    * offset 23 = Действия при перегреве СК (5 бит) + Режим СК (3 бита)
- SYNC: добавлено чтение версии прошивки (offset 43/44) и часов в BCD (offset 57/58/59).
- Блок записи (build_setcfg / set_cfg / _queue_set / _flush_pending_set / looks_like_valid_cfg
  / CFG_OFFSET_* и обработчики команд) НАМЕРЕННО оставлен без изменений.
"""
import os, time, json, socket, threading, datetime, subprocess
import paho.mqtt.client as mqtt
import temzit_cloud

TEMZIT_HOST = os.getenv('TEMZIT_HOST', '192.168.2.20')
TEMZIT_PORT = int(os.getenv('TEMZIT_PORT', '333'))
TEMZIT_TIMEOUT = int(os.getenv('TEMZIT_TIMEOUT', '15'))
TEMZIT_SYNC_INTERVAL = int(os.getenv('TEMZIT_SYNC_INTERVAL', '60'))
TEMZIT_CFG_INTERVAL = int(os.getenv('TEMZIT_CFG_INTERVAL', '900'))
TEMZIT_CFG_DELAY_AFTER_SYNC = int(os.getenv('TEMZIT_CFG_DELAY_AFTER_SYNC', '12'))
TEMZIT_RETRY_DELAY = int(os.getenv('TEMZIT_RETRY_DELAY', '15'))
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.1.50')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER = os.getenv('MQTT_USER', '')
MQTT_PASS = os.getenv('MQTT_PASS', '')
MQTT_PREFIX = os.getenv('MQTT_PREFIX', 'temzit')
MQTT_DISCOVERY_PREFIX = os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant')
MQTT_CLIENT_ID = os.getenv('MQTT_CLIENT_ID', 'temzit-bridge')
# Каталог для бэкапов конфигурации перед записью. По умолчанию /share/temzit (доступен
# пользователю через Samba/File editor/SSH). Если он недоступен — выбирается первый записываемый
# из запасных вариантов (resolve_writable_dir), чтобы файлы НИКОГДА не падали в '/', откуда их не
# достать. Бэкапы НАКАПЛИВАЮТСЯ (уникальные имена + append-журнал), не перезаписываются.
TEMZIT_DATA_DIR = os.getenv('TEMZIT_DATA_DIR', '/share/temzit')
# KILL-SWITCH ЗАПИСИ. Кадр записи 0x35 пока НЕ подтверждён на железе (две попытки исказили
# конфиг по-разному, см. 0.8.2/0.8.3). До выяснения запись ВЫКЛЮЧЕНА по умолчанию — чтобы
# случайная команда из HA не испортила настройки контроллера. Чтение работает всегда.
WRITE_ENABLED = os.getenv('TEMZIT_WRITE_ENABLED', '0').strip().lower() in ('1', 'true', 'yes', 'on')
# Облачный модуль. Пусто = выключен. Интервал не чаще раза в 5 минут.
CLOUD_LOGIN = os.getenv('TEMZIT_CLOUD_LOGIN', '').strip()
CLOUD_SERIAL = os.getenv('TEMZIT_CLOUD_SERIAL', '').strip()
CLOUD_PASS = os.getenv('TEMZIT_CLOUD_PASS', '')
CLOUD_INTERVAL = max(300, int(os.getenv('TEMZIT_CLOUD_INTERVAL', '1800') or 1800))
CLOUD_ENABLED = bool(CLOUD_LOGIN and CLOUD_SERIAL and CLOUD_PASS)
# Запись через облако — отдельный выключатель (по умолчанию выключено) и только при включённом модуле.
CLOUD_WRITE = CLOUD_ENABLED and os.getenv('TEMZIT_CLOUD_WRITE', '0').strip().lower() in ('1', 'true', 'yes', 'on')
CLOUD_WRITE_DEBOUNCE = 3.0      # с: изменения за это время уходят одной отправкой формы
CLOUD_RECHECK_AFTER = 150       # с: повторное чтение после записи (ГМ забирает изменения раз в ~1 мин)
CLOUD_WRITE_NUMBERS = (('P15', 'Темзит Гистерезис отопления (облако)'),
                       ('P16', 'Темзит Гистерезис ГВС (облако)'),
                       ('P10', 'Темзит Макс. ГВС от ТН (облако)'))
CLOUD_WRITE_SWITCHES = (('P11b0', 'Темзит Дезинфекция ГВС (облако)'),
                        ('P11b2', 'Темзит Разморозка в БКН (облако)'))
CLOUD_SCHEDULE_LABELS = (('mode', 'режим'), ('start', 'начало'), ('end', 'конец'), ('room', 'Тдома'),
                         ('water', 'Тводы'), ('dhw_target', 'Тгвс'), ('compressor_limit', 'лимит ККБ'),
                         ('heater', 'ТЭН'), ('dhw_mode', 'ГВС'))
# Сенсоры настроек из облака: (поле формы, имя, единицы). С единицами публикуется число, без — подпись.
CLOUD_SENSORS = [
    ('P15', 'Темзит Гистерезис отопления', '°C'), ('P16', 'Темзит Гистерезис ГВС', '°C'),
    ('P10', 'Темзит Макс. ГВС от ТН', '°C'), ('TAmode', 'Темзит Режим ТА', None),
    ('P11b0', 'Темзит Дезинфекция ГВС', None), ('P11b2', 'Темзит Разморозка в БКН', None),
    ('HardVersion2b1', 'Темзит Учитывать Тул', None), ('HardVersion2b6', 'Темзит Гликоль', None),
    ('GVSDualMode', 'Темзит Выбор ККБ для ГВС', None), ('KKB1Type', 'Темзит Тип ККБ1', None),
    ('KKB2Type', 'Темзит Тип ККБ2', None), ('KKBDualLim', 'Темзит Порог включения ККБ2', None),
    ('P91', 'Темзит Датчик протока', None), ('P71', 'Темзит WiFi термометр', None),
    ('P72', 'Темзит Период связи с сервером', 'min'), ('P60', 'Темзит СК режим', None),
    ('P61', 'Темзит СК дельта включения', '°C'), ('P62', 'Темзит СК дельта выключения', '°C'),
    ('P64', 'Темзит СК перегрев БКН', '°C'),
]
VERSION = '0.12.0'

CMD_SYNC = 0x30
CMD_REQCFG = 0x34
CMD_SETCFG = 0x35
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

EXPECTED_LEN = 64  # и ACTUAL_STATE, и CONFIG_MAIN — ровно 64 байта

# --- Смещения для ЗАПИСИ (НЕ менять: используются в коде записи и guard) ---
CFG_OFFSET_MODE = 0
CFG_OFFSET_ROOM_TARGET = 1
CFG_OFFSET_WATER_TARGET = 2
CFG_OFFSET_AUX_HEATER_MODE = 3
CFG_OFFSET_TEN_ON_OUTDOOR = 4
CFG_OFFSET_KKB_OFF_OUTDOOR = 5
CFG_OFFSET_BACKUP_TYPE = 6
CFG_OFFSET_DHW_TARGET = 7
CFG_OFFSET_DHW_MODE = 8
CFG_OFFSET_COMP_LIMIT = 9
CFG_OFFSET_WEATHER_COMP = 18
CFG_OFFSET_DHW_MAX_COMP = 21
CFG_OFFSET_FLOWMETER = 22

MODE_CODE_TO_HA = {0: 'off', 1: 'heat', 2: 'heat', 3: 'heat', 4: 'cool', 5: 'heat'}
HA_MODE_TO_P1 = {'off': 0, 'heat': 1, 'cool': 4}
HA_MODES = ['off', 'heat', 'cool']
P1_NAMES = {0: 'Стоп', 1: 'Нагрев', 2: 'Быстрый', 3: 'ТЭН', 4: 'Холод', 5: 'Внешний'}
# Режим ТЭНа = младший ниббл offset 3 (значения 0..3 -> проценты).
TEN_MODE_NAMES = {0: '0%', 1: '30%', 2: '60%', 3: '100%'}
DHW_MODE_NAMES = {0: 'Выключен', 1: 'Только ТЭН в баке', 2: 'ТН 10%', 3: 'ТН 20%', 4: 'ТН 30%', 5: 'ТН 40%', 6: 'ТН 50%', 7: 'ТН 60%', 8: 'ТН 70%', 9: 'ТН 80%', 10: 'ТН 90%', 11: 'ТН 100%'}
COMP_LIMIT_NAMES = {0: 'Без ограничений', 1: '10%', 2: '20%', 3: '30%', 4: '40%', 5: '50%', 6: '55%', 7: '60%', 8: '70%', 9: '80%', 10: '90%'}
COMP_LIMIT_PCT = {0: 0, 1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 55, 7: 60, 8: 70, 9: 80, 10: 90}
# Режим внешнего котла (дизель) = offset 8 (бывший backup_type).
EXTERNAL_BOILER_NAMES = {0: 'Не использовать', 1: 'после I ступени', 2: 'после II ступени', 3: 'после III ступени', 4: 'только внешний'}
# Подтверждено по веб-интерфейсу: значение 5 = 'Электронный 4р'. Остальные значения пока не подтверждены замером.
FLOWMETER_TYPES = {0: 'unknown', 1: 'impulse_1l?', 2: 'impulse_10l?', 3: 'dual_channel?', 4: 'electronic?', 5: 'Электронный 4р', 6: 'reed_switch?'}
# Коды ошибок по официальному приложению (app.js). Поле аварий 32-битное; биты ККБ2 — старшее слово.
ALARM_BITS = {
    0x00000001: 'E05 контактор ТЭН', 0x00000002: 'E08 нет связи с ККБ1', 0x00000004: 'E01 низкий проток канал 1',
    0x00000008: 'E07 нет связи с WiFi-датчиком', 0x00000010: 'E09 ККБ1 не переключается в нагрев',
    0x00000020: 'E02 высокая Т фреона газ канал 1', 0x00000040: 'E03 низкая Т фреона жидк канал 1',
    0x00000080: 'E04 авария ККБ1', 0x00000100: 'E06 сброс часов/расписания',
    0x00004000: 'E0F критичная неисправность датчика T', 0x00008000: 'E0G неверная прошивка дисплея',
    0x00020000: 'E18 нет связи с ККБ2', 0x00040000: 'E11 низкий проток канал 2',
    0x00100000: 'E19 ККБ2 не переключается в нагрев', 0x00200000: 'E12 высокая Т фреона газ канал 2',
    0x00400000: 'E13 низкая Т фреона жидк канал 2', 0x00800000: 'E14 авария ККБ2',
}
# Имена живого состояния (SYNC offset 0), по app.js. Это НЕ конфигурационный режим P1.
STATE_MODE_NAMES = ['СТОП', 'НАГРЕВ', 'ГВС', 'ТЭН']


def s8(v):
    return None if v is None else (v if v < 128 else v - 256)


def u8(v):
    return int(v) & 0xFF


def bcd(v):
    return None if v is None else (v >> 4) * 10 + (v & 0x0F)


def weather_comp_from_raw(v):
    return None if v is None else round(v * 0.1, 1)


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def u16le(buf: bytes, off: int):
    if len(buf) < off + 2:
        return None
    return int.from_bytes(buf[off:off + 2], 'little', signed=False)


def u32le(buf: bytes, off: int):
    if len(buf) < off + 4:
        return None
    return int.from_bytes(buf[off:off + 4], 'little', signed=False)


def s16le(buf: bytes, off: int):
    v = u16le(buf, off)
    return None if v is None else (v - 65536 if v >= 32768 else v)


def heater_stage_from_raw(v):
    if v is None:
        return None
    if 9 <= v <= 84:
        return 1
    if 85 <= v <= 169:
        return 2
    if 170 <= v <= 255:
        return 3
    return 0


def dhw_heater_on(v):
    return None if v is None else ('ON' if (v & 0x1) else 'OFF')


def decode_alarm(v):
    if v is None:
        return None
    names = [name for bit, name in ALARM_BITS.items() if v & bit]
    return 'ok' if not names else ','.join(names)


def resolve_writable_dir(preferred):
    """Первый каталог, в который реально удаётся писать (создаётся при необходимости).
    Нужен, чтобы бэкапы не падали в '/', откуда их не достать. Порядок: предпочитаемый ->
    /share/temzit (доступен пользователю) -> /config/temzit -> /data/temzit (персистентно для
    аддона) -> ./temzit_backups. Возвращает путь или None, если писать некуда."""
    candidates = []
    for d in [preferred, '/share/temzit', '/config/temzit', '/data/temzit',
              os.path.join(os.getcwd(), 'temzit_backups')]:
        if d and d not in candidates:
            candidates.append(d)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.write_test')
            with open(probe, 'w') as f:
                f.write('ok')
            os.remove(probe)
            return d
        except Exception:
            continue
    return None


# ============================================================================
# БЛОК ЗАПИСИ
# ============================================================================
def build_setcfg(cfg_bytes: list) -> bytes:
    """Кадр записи 0x35 — ПОДТВЕРЖДЁН на железе по дисплею ГМ (v0.8.7). Ровно 32 байта:
        frame = [0x35, f1, config[0..29]]
    Контроллер:
      - настройки (offset 0..29, включая Режим) читает из frame[2:32];
      - в frame[31] (это и есть config[29]) ПРОВЕРЯЕТ КС = sum(frame[0:31]) & 0xFF.
    Байт frame[1] в настройки не идёт — это «настроечный» байт f1, которым мы добиваемся, чтобы
    КС сошлась с config[29]:
        f1 = (config[29] - 0x35 - sum(config[0:29])) & 0xFF
    Прежние версии клали настройки сразу после 0x35 и/или с неверной КС → сдвиг на байт или
    отказ записи."""
    if len(cfg_bytes) != 30:
        raise ValueError(f'build_setcfg expects 30 cfg bytes, got {len(cfg_bytes)}')
    cfg = [u8(x) for x in cfg_bytes]
    f1 = (cfg[29] - CMD_SETCFG - sum(cfg[0:29])) & 0xFF
    return bytes([CMD_SETCFG, f1] + cfg)


def looks_like_valid_cfg(cfg_raw):
    if cfg_raw is None or len(cfg_raw) != 30:
        return False, 'len'
    mode = cfg_raw[CFG_OFFSET_MODE]
    room = cfg_raw[CFG_OFFSET_ROOM_TARGET]
    water = cfg_raw[CFG_OFFSET_WATER_TARGET]
    aux = cfg_raw[CFG_OFFSET_AUX_HEATER_MODE]
    dhw_mode = cfg_raw[CFG_OFFSET_DHW_MODE]
    comp_limit = cfg_raw[CFG_OFFSET_COMP_LIMIT]
    if mode not in (0, 1, 2, 3, 4, 5):
        return False, f'mode={mode}'
    if not (5 <= room <= 45):
        return False, f'room={room}'
    if not (5 <= water <= 70):
        return False, f'water={water}'
    if aux not in (64, 65, 66, 67):
        return False, f'aux={aux}'
    if not (0 <= dhw_mode <= 11):
        return False, f'dhw_mode={dhw_mode}'
    if not (0 <= comp_limit <= 10):
        return False, f'comp_limit={comp_limit}'
    return True, 'ok'
# ============================================================================


class TemzitClient:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.lock = threading.Lock()

    @staticmethod
    def _recv_until(s, n):
        """Дочитываем поток до n байт (TCP может отдать ответ несколькими сегментами)."""
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                break
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def _query(self, payload: bytes) -> tuple:
        with self.lock:
            t0 = time.time()
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(payload)
                data = self._recv_until(s, EXPECTED_LEN)
            dt = round((time.time() - t0) * 1000)
            return data, dt

    def get_sync(self) -> dict:
        data, dt = self._query(bytes([CMD_SYNC, 0x00]))
        if len(data) < EXPECTED_LEN:
            raise ValueError(f'incomplete sync reply: {len(data)} bytes')
        if data[0] != RESP_ACTUAL:
            raise ValueError(f'unexpected sync reply type: {data[0]}')
        crc_rx = int.from_bytes(data[62:64], 'little')
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'sync CRC mismatch: rx={crc_rx} calc={crc_calc}')
        p = data[2:62]

        def t(off):
            v = s16le(p, off)  # температуры знаковые (улица/фреон могут быть < 0)
            return None if v is None else v / 10.0

        flow_raw = u16le(p, 18)
        heater_state = u16le(p, 24)
        dhw_state = u16le(p, 26)
        alarm = u32le(p, 30)
        mode_code = u16le(p, 0)
        power_raw = u16le(p, 28)
        set_compressor_limit_raw = p[52] if len(p) > 52 else None
        fw_major = p[43] if len(p) > 43 else None
        fw_minor = p[44] if len(p) > 44 else None
        hh = bcd(p[57]) if len(p) > 57 else None
        mm = bcd(p[58]) if len(p) > 58 else None
        ss = bcd(p[59]) if len(p) > 59 else None
        clock = None if None in (hh, mm, ss) else f'{hh:02d}:{mm:02d}:{ss:02d}'
        return {
            'diag_sync_len': len(data), 'diag_sync_ms': dt, '_sync_raw': list(p),
            'mode_code': mode_code, 'mode_name': STATE_MODE_NAMES[mode_code & 0xFF] if (mode_code & 0xFF) < len(STATE_MODE_NAMES) else f'mode_{mode_code}',
            'schedule_no': u16le(p, 2), 't_outdoor': t(4), 't_room': t(6), 't_supply': t(8), 't_return': t(10),
            't_freon_gas': t(12), 't_freon_liquid': t(14), 't_dhw': t(16),
            'flow_raw': flow_raw, 'flow_l_min': None if flow_raw is None else round(flow_raw * 4, 2),
            'compressor_type': p[20] if len(p) > 20 else None, 'compressor_model': p[21] if len(p) > 21 else None,
            'compressor_hz_1': p[22] if len(p) > 22 else None, 'compressor_hz_2': p[23] if len(p) > 23 else None,
            'compressor_active': 'ON' if ((p[22] if len(p) > 22 else 0) or 0) > 0 or ((p[23] if len(p) > 23 else 0) or 0) > 0 else 'OFF',
            'heater_state_raw': heater_state, 'heater_stage': heater_stage_from_raw(heater_state),
            'dhw_heater_state_raw': dhw_state, 'dhw_heater_on': dhw_heater_on(dhw_state),
            'power_kw': None if power_raw is None else round(power_raw / 10.0, 1), 'alarm': alarm, 'alarm_text': decode_alarm(alarm),
            'fw_major': fw_major, 'fw_minor': fw_minor,
            'fw_version': None if None in (fw_major, fw_minor) else f'{fw_major}.{fw_minor}',
            'active_schedule_no': p[45] if len(p) > 45 else None, 'active_schedule_mode': p[46] if len(p) > 46 else None,
            'active_schedule_name': ('Основное' if (p[45] if len(p) > 45 else None) == 0 else f'Расписание {p[45]}') if len(p) > 45 else None,
            # Эхо активного расписания (подтверждено дампом): Тдома=49, Тводы=50, Тгвс=51, огр.ККБ=52, реж.ТЭНа=53, реж.ГВС=54
            'set_room': p[49] if len(p) > 49 else None, 'set_water': p[50] if len(p) > 50 else None, 'set_dhw': p[51] if len(p) > 51 else None,
            'set_compressor_limit': set_compressor_limit_raw, 'set_compressor_limit_pct': COMP_LIMIT_PCT.get(set_compressor_limit_raw),
            'set_compressor_limit_name': COMP_LIMIT_NAMES.get(set_compressor_limit_raw, str(set_compressor_limit_raw)),
            'set_ten_mode': p[53] if len(p) > 53 else None, 'set_ten_mode_name': TEN_MODE_NAMES.get(p[53] if len(p) > 53 else None, '?'),
            'set_dhw_mode': p[54] if len(p) > 54 else None,
            'set_dhw_mode_name': DHW_MODE_NAMES.get(p[54] if len(p) > 54 else None, '?'),
            'weekday': p[56] if len(p) > 56 else None, 'hour': hh, 'minute': mm, 'second': ss, 'clock': clock,
        }

    def get_cfg(self) -> dict:
        data, dt = self._query(bytes([CMD_REQCFG, 0x00]))
        if len(data) < EXPECTED_LEN:
            raise ValueError(f'incomplete cfg reply: {len(data)} bytes')
        if data[0] != RESP_CONFIG:
            raise ValueError(f'unexpected cfg reply type: {data[0]}')
        crc_rx = int.from_bytes(data[62:64], 'little')
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'cfg CRC mismatch: rx={crc_rx} calc={crc_calc}')
        p = list(data[2:32])
        if len(p) != 30:
            raise ValueError(f'cfg payload must be exactly 30 bytes, got {len(p)}')

        # offset 3: инерция дома (hi) + режим ТЭНа (lo)
        b3 = p[3]
        house_inertia = b3 >> 4
        ten_mode = b3 & 0x0F
        # offset 6: дезинфекция (hi) + режим ГВС (lo)
        b6 = p[6]
        disinfection = b6 >> 4
        dhw_mode = b6 & 0x0F
        # offset 8: режим внешнего котла (дизель)
        external_boiler = p[8]
        comp_limit_raw = p[CFG_OFFSET_COMP_LIMIT]
        weather_raw = p[CFG_OFFSET_WEATHER_COMP]
        # offset 19: Тколлектор выкл (hi) + вкл (lo)
        b19 = p[19]
        collector_off = b19 >> 4
        collector_on = b19 & 0x0F
        # offset 23: действия при перегреве СК (5 бит) + режим СК (3 бита)
        b23 = p[23]
        sk_overheat_action = b23 >> 3
        sk_mode = b23 & 0x07
        flowmeter_raw = p[CFG_OFFSET_FLOWMETER]

        return {
            'diag_cfg_len': len(data), 'diag_cfg_ms': dt,
            'cfg_mode': p[0], 'cfg_mode_name': P1_NAMES.get(p[0], str(p[0])),
            'cfg_room_target': p[1], 'cfg_water_target': p[2],
            'cfg_house_inertia': house_inertia,
            'cfg_ten_mode': ten_mode, 'cfg_ten_mode_name': TEN_MODE_NAMES.get(ten_mode, str(ten_mode)),
            'cfg_ten_on_outdoor': s8(p[4]), 'cfg_kkb_min_outdoor': s8(p[5]),
            'cfg_disinfection': disinfection,
            'cfg_dhw_mode': dhw_mode, 'cfg_dhw_mode_name': DHW_MODE_NAMES.get(dhw_mode, str(dhw_mode)),
            'cfg_dhw_target': p[7],
            'cfg_external_boiler': external_boiler, 'cfg_external_boiler_name': EXTERNAL_BOILER_NAMES.get(external_boiler, str(external_boiler)),
            'cfg_compressor_limit': comp_limit_raw, 'cfg_compressor_limit_pct': COMP_LIMIT_PCT.get(comp_limit_raw), 'cfg_compressor_limit_name': COMP_LIMIT_NAMES.get(comp_limit_raw, str(comp_limit_raw)),
            'cfg_elec_pulses': p[17], 'cfg_elec_pulses_per_kwh': p[17] * 100,  # сверка с веб: 16 -> 1600 имп/кВт·ч
            'cfg_weather_comp': weather_comp_from_raw(weather_raw),
            'cfg_collector_off': collector_off, 'cfg_collector_on': collector_on,
            'cfg_pump_relay_mode': p[20],
            'cfg_dhw_max_from_compressor': p[21],
            'cfg_flowmeter_type': flowmeter_raw, 'cfg_flowmeter_type_name': FLOWMETER_TYPES.get(flowmeter_raw, f'unknown_{flowmeter_raw}'),
            'cfg_sk_overheat_action': sk_overheat_action, 'cfg_sk_mode': sk_mode,
            'cfg_ta_overheat_temp': p[24], 'cfg_kkb1_type': p[25],
            # 10..16 — настройки WiFi-контроллера, менять извне запрещено; отдаём как диагностику
            'cfg_wifi_bytes': p[10:17],
            # 26..29 — недокументировано в протоколе, отдаём сырыми для дальнейшего реверса
            'cfg_undoc_26': p[26], 'cfg_undoc_27': p[27], 'cfg_undoc_28': p[28], 'cfg_undoc_29': p[29],
            '_raw': p,
        }

    def set_cfg(self, cfg_raw: list, updates: dict) -> dict:
        new_cfg = list(cfg_raw)
        for offset, value in updates.items():
            if not (0 <= offset < len(new_cfg)):
                raise ValueError(f'offset out of range: {offset}')
            new_cfg[offset] = u8(value)
        packet = build_setcfg(new_cfg)
        print(f'set_cfg packet ({len(packet)} bytes): {list(packet)}', flush=True)
        # ВАЖНО: запись отправляем через `nc`, а НЕ через питон-сокет. По захвату Wireshark
        # питон-сокет с идентичными байтами кадра давал контроллеру сдвиг на 2 байта (и контроллер
        # не отвечал), а `printf | nc` с теми же байтами — пишет верно. Поэтому буквально
        # повторяем рабочий способ: пайпим кадр в nc (нужен netcat-openbsd в образе).
        with self.lock:
            try:
                proc = subprocess.run(
                    ['nc', '-w', str(self.timeout), self.host, str(self.port)],
                    input=packet, capture_output=True, timeout=self.timeout + 5)
                resp = proc.stdout or b''
                print(f'set_cfg(nc) rc={proc.returncode} response ({len(resp)} bytes): {list(resp)}', flush=True)
                if proc.stderr:
                    print(f'set_cfg(nc) stderr: {proc.stderr.decode("utf-8", "ignore").strip()}', flush=True)
            except FileNotFoundError:
                raise RuntimeError("'nc' не найден в образе (нужен netcat-openbsd) — запись через nc невозможна")
            except Exception as ex:
                print(f'set_cfg(nc) error: {ex}', flush=True)
                raise
        return {'packet': list(packet), 'response': list(resp), 'cfg_raw': cfg_raw, 'new_cfg': new_cfg, 'updates': updates}


def make_mqtt_client():
    """Создаёт MQTT-клиент совместимо с paho-mqtt 1.x и 2.x.
    paho 1.x: Client(client_id=..., clean_session=...).
    paho 2.x: первым позиционным аргументом идёт callback_api_version — используем VERSION1,
    чтобы сигнатуры колбэков (on_connect(client, userdata, flags, rc)) не пришлось менять.
    Прод-образ аддона на Alpine 3.19 несёт paho 1.6.1; компат нужен на случай иной версии."""
    try:
        from paho.mqtt.client import CallbackAPIVersion
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=MQTT_CLIENT_ID, clean_session=True)
    except (ImportError, AttributeError):
        return mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)


class Bridge:
    def __init__(self):
        self.temzit = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = make_mqtt_client()
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.discovery_sent = False
        self.last_cfg_poll = 0
        self.last_sync_ts = 0
        self._last_cfg_raw = None
        self._last_good_cfg_raw = None
        self._pending_set = {}
        self._set_lock = threading.Lock()
        self._last_sync_raw = None
        # Облако: _cloud_io сериализует сетевые обращения (опрос и запись), _cloud_lock — очередь записи.
        self._cloud_io = threading.Lock()
        self._cloud_lock = threading.Lock()
        self._cloud_pending = {}
        self._cloud_timer = None
        self._cloud_sched_options = {}
        self._cloud_write_discovered = False
        self.backup_dir = resolve_writable_dir(TEMZIT_DATA_DIR)
        if self.backup_dir:
            print(f'CFG backup dir: {self.backup_dir}', flush=True)
        else:
            print('WARNING: нет записываемого каталога для бэкапов — запись будет ЗАБЛОКИРОВАНА', flush=True)
        # Режим «Облачный/Локальный» (переключатель в HA), запоминается в data_dir/cloud_mode.json.
        self._cloud_wake = threading.Event()
        self.cloud_on = self._load_cloud_mode()

    def publish(self, topic, payload, retain=True, qos=0):
        # HA 2026.4+ игнорирует obj_id в MQTT discovery: идентификатор сущности задаётся только полем
        # default_entity_id вида '<домен>.<имя>'. Иначе HA строит entity_id из русского названия.
        if isinstance(payload, dict) and 'obj_id' in payload and topic.startswith(f'{MQTT_DISCOVERY_PREFIX}/'):
            payload = dict(payload)
            payload['default_entity_id'] = f"{topic.split('/')[1]}.{payload.pop('obj_id')}"
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def _discover(self, platform, key, cfg):
        """Discovery новых сущностей (ctl_*, cloud_*) под ревизией _v2.
        До 0.11.2 в HA 2026.4+ они создавались с entity_id из русских названий (obj_id игнорируется).
        Пустое retained-сообщение в старый топик удаляет такую сущность из HA, а публикация под новым
        unique_id создаёт её заново с entity_id из default_entity_id ('<домен>.<key>').
        Публикация отложена до _flush_discovery(): сначала стираем всё, ждём, потом создаём —
        чтобы HA успел освободить старые id (иначе вручную переименованные получат суффикс _2)."""
        if not hasattr(self, '_disc_batch'):
            self._disc_batch = []
        self._disc_batch.append((platform, key, dict(cfg, uniq_id=f'{key}_v2', obj_id=key)))

    def _flush_discovery(self, pause=3.0):
        batch, self._disc_batch = getattr(self, '_disc_batch', []), []
        if not batch:
            return
        for platform, key, _ in batch:
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/{platform}/{key}/config', '')
        time.sleep(pause)
        for platform, key, cfg in batch:
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/{platform}/{key}_v2/config', cfg)

    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_mode')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_temperature')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_water_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_dhw_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_compressor_limit')
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_byte')
        client.subscribe(f'{MQTT_PREFIX}/cmd/restore_raw')
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_mode_name')
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_compressor_limit_name')
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_weather_comp')
        if CLOUD_WRITE:
            client.subscribe(f'{MQTT_PREFIX}/cloud/set/#')
        if CLOUD_ENABLED:
            client.subscribe(f'{MQTT_PREFIX}/cloud/mode/set')

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        try:
            if topic == f'{MQTT_PREFIX}/cloud/mode/set':
                self._set_cloud_mode(payload)
            elif topic.startswith(f'{MQTT_PREFIX}/cloud/set/'):
                self._handle_cloud_cmd(topic, payload)
            else:
                self._handle_cmd(topic, payload)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'cmd_error': str(e), 'topic': topic, 'payload': payload})

    def _handle_cmd(self, topic, payload):
        suffix = topic.split('/')[-1]
        if suffix == 'set_mode':
            # Режим (offset 0) кадром 0x35 ПИШЕТСЯ (он во frame[2], подтверждено на железе).
            p1 = HA_MODE_TO_P1.get(payload.lower())
            if p1 is None:
                raise ValueError(f'Unknown mode: {payload}')
            self._queue_set(CFG_OFFSET_MODE, p1)
        elif suffix == 'set_temperature':
            self._queue_set(CFG_OFFSET_ROOM_TARGET, max(16, min(30, round(float(payload)))))
        elif suffix == 'set_water_temp':
            self._queue_set(CFG_OFFSET_WATER_TARGET, max(5, min(55, round(float(payload)))))
        elif suffix == 'set_dhw_temp':
            self._queue_set(CFG_OFFSET_DHW_TARGET, max(20, min(70, round(float(payload)))))
        elif suffix == 'set_compressor_limit':
            self._queue_set(CFG_OFFSET_COMP_LIMIT, max(0, min(10, round(float(payload)))))
        elif suffix == 'set_mode_name':
            # Полный список режимов P1 (селектор на карточке): подпись -> код.
            code = {v: k for k, v in P1_NAMES.items()}.get(payload)
            if code is None:
                raise ValueError(f'Unknown mode name: {payload}')
            self._queue_set(CFG_OFFSET_MODE, code)
        elif suffix == 'set_compressor_limit_name':
            code = {v: k for k, v in COMP_LIMIT_NAMES.items()}.get(payload)
            if code is None:
                raise ValueError(f'Unknown compressor limit: {payload}')
            self._queue_set(CFG_OFFSET_COMP_LIMIT, code)
        elif suffix == 'set_weather_comp':
            # Погодокомпенсация 0.0..1.0 -> байт 0..10 (как в форме сервера).
            self._queue_set(CFG_OFFSET_WEATHER_COMP, max(0, min(10, round(float(payload) * 10))))
        elif suffix == 'set_byte':
            data = json.loads(payload)
            self._queue_set(int(data['offset']), int(data['value']))
        elif suffix == 'restore_raw':
            # Полное восстановление 30 байт: список целых [..30..] или hex-строка (60 символов).
            data = json.loads(payload)
            raw = list(bytes.fromhex(data)) if isinstance(data, str) else [int(x) for x in data]
            self._restore_cfg(raw)

    def _queue_set(self, offset: int, value: int):
        if not WRITE_ENABLED:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'write_disabled': 'Запись отключена (write_enabled=false): кадр 0x35 не подтверждён на железе. Меняй настройки с панели ГМ.', 'offset': offset, 'value': value})
            print(f'WRITE DISABLED: игнорирую set offset={offset} value={value}', flush=True)
            return
        with self._set_lock:
            self._pending_set[offset] = value
        self._flush_pending_set()

    def _flush_pending_set(self):
        with self._set_lock:
            if not self._pending_set:
                return
            if self._last_cfg_raw is None and self._last_good_cfg_raw is None:
                print('CFG not yet loaded, forcing poll before applying pending set', flush=True)
                threading.Thread(target=self._force_cfg_then_flush, daemon=True).start()
                return
            updates = dict(self._pending_set)
            self._pending_set.clear()

        current_ok, current_reason = looks_like_valid_cfg(self._last_cfg_raw)
        good_ok, good_reason = looks_like_valid_cfg(self._last_good_cfg_raw)
        if current_ok:
            base_cfg = list(self._last_cfg_raw)
            chosen = 'last_cfg_raw'
        elif good_ok:
            base_cfg = list(self._last_good_cfg_raw)
            chosen = 'last_good_cfg_raw'
        else:
            self.publish(f'{MQTT_PREFIX}/diag/set_guard', {
                'status': 'blocked', 'reason_current': current_reason, 'reason_good': good_reason,
                'last_cfg_raw': self._last_cfg_raw, 'last_good_cfg_raw': self._last_good_cfg_raw, 'updates': updates,
            }, retain=False)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': 'No valid cfg_raw for safe write', 'updates': str(updates)})
            return

        # Применяем обновления к копии и проверяем РЕЗУЛЬТАТ (а не только источник).
        new_cfg = list(base_cfg)
        for off, val in updates.items():
            if not (0 <= off < len(new_cfg)):
                self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': f'offset out of range: {off}', 'updates': str(updates)})
                return
            new_cfg[off] = u8(val)

        # Дедупликация: ничего реально не меняется — НЕ пишем (бережём ресурс flash).
        if new_cfg == list(base_cfg):
            self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'nochange', 'updates': updates, 'base_cfg': base_cfg}, retain=False)
            print(f'set_cfg skipped (no change): {updates}', flush=True)
            return

        # Валидация РЕЗУЛЬТАТА: не дать записать нереалистичные параметры (они роняли контроллер).
        new_ok, new_reason = looks_like_valid_cfg(new_cfg)
        if not new_ok:
            self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'rejected', 'reason': new_reason, 'updates': updates, 'base_cfg': base_cfg, 'new_cfg': new_cfg}, retain=False)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': f'resulting cfg invalid: {new_reason}', 'updates': str(updates)})
            print(f'set_cfg REJECTED (invalid result {new_reason}): {updates}', flush=True)
            return

        # ОБЯЗАТЕЛЬНЫЙ бэкап текущего рабочего дампа ПЕРЕД записью. Нет бэкапа -> нет записи.
        try:
            backup_path = self._backup_cfg(base_cfg, reason='pre_write', updates=updates, new_cfg=new_cfg)
        except Exception as be:
            self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'backup_failed', 'error': str(be), 'updates': updates}, retain=False)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': f'backup failed, write aborted: {be}', 'updates': str(updates)})
            print(f'set_cfg ABORTED (backup failed: {be}) updates={updates}', flush=True)
            return

        self.publish(f'{MQTT_PREFIX}/diag/set_guard', {
            'status': 'using', 'chosen': chosen, 'updates': updates, 'base_cfg': base_cfg, 'new_cfg': new_cfg,
            'backup': backup_path, 'current_ok': current_ok, 'good_ok': good_ok,
        }, retain=False)

        try:
            result = self.temzit.set_cfg(base_cfg, updates)
            result['backup'] = backup_path
            self.publish(f'{MQTT_PREFIX}/diag/last_set', result, retain=False)
            print(f'set_cfg OK: {updates} (backup={backup_path})', flush=True)
            threading.Timer(2.0, self._force_sync_and_cfg).start()
        except Exception as e:
            print(f'set_cfg ERROR: {e} updates={updates}', flush=True)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': str(e), 'updates': str(updates)})
            with self._set_lock:
                updates.update(self._pending_set)
                self._pending_set = updates

    def _backup_cfg(self, cfg_raw, reason, updates=None, new_cfg=None):
        """Накопительный бэкап рабочего дампа ПЕРЕД записью. Пишет два артефакта:
          - отдельный файл cfg_<timestamp>.json (уникальное имя -> прежние бэкапы не теряются),
          - строку в общий журнал temzit_cfg_history.jsonl (append-only, тоже накопление).
        Возвращает путь к файлу бэкапа. Бросает исключение, если записать не удалось — тогда
        вызывающий код ОТМЕНЯЕТ запись (нет бэкапа -> нет записи)."""
        if not self.backup_dir:
            self.backup_dir = resolve_writable_dir(TEMZIT_DATA_DIR)
        if not self.backup_dir:
            raise RuntimeError('нет записываемого каталога для бэкапа')
        ts = datetime.datetime.now()
        rec = {
            'ts': ts.isoformat(timespec='seconds'),
            'reason': reason,
            'version': VERSION,
            'cfg_raw': list(cfg_raw),
            'cfg_hex': bytes(u8(x) for x in cfg_raw).hex(),
            'cfg_valid': looks_like_valid_cfg(cfg_raw)[0],
            'updates': updates,
            'new_cfg': new_cfg,
            'sync_raw': self._last_sync_raw,
        }
        os.makedirs(self.backup_dir, exist_ok=True)
        # 1) отдельный файл бэкапа — уникальное имя с микросекундами (не перезаписывает прежние)
        fname = f'cfg_{ts.strftime("%Y%m%d_%H%M%S_%f")}.json'
        path = os.path.join(self.backup_dir, fname)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        # 2) накопительный журнал (append-only) — полная история всех бэкапов в одном файле
        hist = os.path.join(self.backup_dir, 'temzit_cfg_history.jsonl')
        with open(hist, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self.publish(f'{MQTT_PREFIX}/diag/backup', {'path': path, 'history': hist, 'ts': rec['ts'], 'reason': reason}, retain=False)
        print(f'CFG backup written: {path}', flush=True)
        return path

    def _restore_cfg(self, raw):
        """Полное восстановление 30 байт конфигурации (например, из бэкапа). В отличие от
        обычной записи — БЕЗ дедупликации (всегда пишем), т.к. цель именно перезаписать конфиг
        устройства целиком. Бэкап текущего состояния и валидация результата обязательны."""
        if not WRITE_ENABLED:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'write_disabled': 'Запись отключена (write_enabled=false): кадр 0x35 не подтверждён на железе. Восстанавливай с панели ГМ или docs/temzit_restore.py.'})
            print('WRITE DISABLED: игнорирую restore_raw', flush=True)
            return
        if not isinstance(raw, list) or len(raw) != 30:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'restore_error': f'нужно ровно 30 байт, получено {len(raw) if hasattr(raw, "__len__") else "?"}'})
            return
        raw = [u8(x) for x in raw]
        ok, reason = looks_like_valid_cfg(raw)
        if not ok:
            self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'restore_rejected', 'reason': reason, 'raw': raw}, retain=False)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'restore_error': f'невалидный конфиг: {reason}'})
            return
        # Бэкап текущего (возможно повреждённого) состояния устройства перед восстановлением.
        try:
            backup_path = self._backup_cfg(self._last_cfg_raw or raw, reason='pre_restore', new_cfg=raw)
        except Exception as be:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'restore_error': f'бэкап не удался, восстановление отменено: {be}'})
            return
        self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'restoring', 'raw': raw, 'backup': backup_path}, retain=False)
        try:
            result = self.temzit.set_cfg(raw, {})  # пишем ровно raw (build_setcfg(raw))
            result['backup'] = backup_path
            self.publish(f'{MQTT_PREFIX}/diag/last_set', result, retain=False)
            print(f'restore OK (backup={backup_path})', flush=True)
            threading.Timer(2.0, self._force_sync_and_cfg).start()
        except Exception as e:
            print(f'restore ERROR: {e}', flush=True)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'restore_error': str(e)})

    def _force_cfg_then_flush(self):
        now = time.time()
        wait = self.last_sync_ts + TEMZIT_CFG_DELAY_AFTER_SYNC - now
        if wait > 0:
            print(f'_force_cfg_then_flush: waiting {wait:.1f}s before CFG query', flush=True)
            time.sleep(wait)
        try:
            cfg = self.temzit.get_cfg()
            self._publish_cfg(cfg)
            self.last_cfg_poll = time.time()
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'force_cfg_error': str(e)})
            return
        self._flush_pending_set()

    def _force_sync(self):
        try:
            state = self.temzit.get_sync()
            self.last_sync_ts = time.time()
            self._publish_state(state)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'force_sync_error': str(e)})

    def _force_sync_and_cfg(self):
        self._force_sync()
        print(f'_force_sync_and_cfg: waiting {TEMZIT_CFG_DELAY_AFTER_SYNC}s before CFG query', flush=True)
        time.sleep(TEMZIT_CFG_DELAY_AFTER_SYNC)
        try:
            cfg = self.temzit.get_cfg()
            self._publish_cfg(cfg)
            self.last_cfg_poll = time.time()
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'force_cfg_error': str(e)})

    def publish_discovery(self):
        if self.discovery_sent:
            return
        device = {'identifiers': ['temzit_hp_1'], 'name': 'Temzit Heat Pump', 'manufacturer': 'ТЭМЗИТ', 'model': 'Hydromodule', 'sw_version': VERSION}
        avail = {'availability_topic': f'{MQTT_PREFIX}/availability', 'payload_available': 'online', 'payload_not_available': 'offline'}
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_climate/config', {'name': 'Temzit', 'uniq_id': 'temzit_climate', 'obj_id': 'temzit_climate', 'device': device, **avail, 'curr_temp_t': f'{MQTT_PREFIX}/state/t_room', 'temp_stat_t': f'{MQTT_PREFIX}/state/climate_target_temp', 'temp_cmd_t': f'{MQTT_PREFIX}/climate/set_temperature', 'temp_step': 1, 'min_temp': 16, 'max_temp': 30, 'mode_stat_t': f'{MQTT_PREFIX}/state/ha_mode', 'mode_cmd_t': f'{MQTT_PREFIX}/climate/set_mode', 'modes': HA_MODES, 'precision': 0.1})
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_dhw_climate/config', {'name': 'Temzit ГВС', 'uniq_id': 'temzit_dhw_climate', 'obj_id': 'temzit_dhw_climate', 'device': device, **avail, 'curr_temp_t': f'{MQTT_PREFIX}/state/t_dhw', 'temp_stat_t': f'{MQTT_PREFIX}/state/cfg_dhw_target', 'temp_cmd_t': f'{MQTT_PREFIX}/climate/set_dhw_temp', 'temp_step': 1, 'min_temp': 20, 'max_temp': 70, 'mode_stat_t': f'{MQTT_PREFIX}/state/dhw_ha_mode', 'mode_cmd_t': f'{MQTT_PREFIX}/climate/set_mode', 'modes': ['off', 'heat'], 'precision': 1})

        # Автообнаружение датчиков (read-only): (ключ_топика, имя, единицы, device_class)
        sensors = [
            ('t_outdoor', 'Темзит Улица', '°C', 'temperature'),
            ('t_room', 'Темзит В доме', '°C', 'temperature'),
            ('t_supply', 'Темзит Подача', '°C', 'temperature'),
            ('t_return', 'Темзит Обратка', '°C', 'temperature'),
            ('t_dhw', 'Темзит ГВС', '°C', 'temperature'),
            ('t_freon_gas', 'Темзит Фреон газ', '°C', 'temperature'),
            ('t_freon_liquid', 'Темзит Фреон жидкость', '°C', 'temperature'),
            ('power_kw', 'Темзит Потребление', 'kW', 'power'),
            ('compressor_hz_1', 'Темзит Компрессор частота', 'Hz', 'frequency'),
            ('flow_l_min', 'Темзит Проток', 'L/min', None),
            ('mode_name', 'Темзит Состояние', None, None),
            ('compressor_active', 'Темзит Компрессор', None, None),
            ('alarm_text', 'Темзит Авария', None, None),
            ('fw_version', 'Темзит Версия ПО', None, None),
            ('active_schedule_name', 'Темзит Активное расписание', None, None),
            ('set_room', 'Темзит Уставка дома (активн.)', '°C', 'temperature'),
            ('set_water', 'Темзит Уставка воды (активн.)', '°C', 'temperature'),
            ('set_dhw', 'Темзит Уставка ГВС (активн.)', '°C', 'temperature'),
            ('set_ten_mode_name', 'Темзит Расписание режим ТЭНа', None, None),
            ('set_dhw_mode_name', 'Темзит Расписание режим ГВС', None, None),
            ('set_compressor_limit_name', 'Темзит Расписание огранич. ККБ', None, None),
            ('cfg_room_target', 'Темзит Уставка дома', '°C', 'temperature'),
            ('cfg_water_target', 'Темзит Уставка воды', '°C', 'temperature'),
            ('cfg_dhw_target', 'Темзит Уставка ГВС (конфиг)', '°C', 'temperature'),
            ('cfg_dhw_mode_name', 'Темзит Режим ГВС', None, None),
            ('cfg_ten_mode_name', 'Темзит Режим ТЭНа', None, None),
            ('cfg_external_boiler_name', 'Темзит Внешний котёл', None, None),
            ('cfg_compressor_limit_name', 'Темзит Ограничение ККБ', None, None),
            ('cfg_weather_comp', 'Темзит Погодокомпенсация', None, None),
        ]
        for key, name, unit, dev_cla in sensors:
            cfg = {'name': name, 'uniq_id': f'temzit_{key}', 'obj_id': f'temzit_{key}', 'device': device, **avail,
                   'stat_t': f'{MQTT_PREFIX}/state/{key}'}
            if unit:
                cfg['unit_of_meas'] = unit
                cfg['stat_cla'] = 'measurement'
            if dev_cla:
                cfg['dev_cla'] = dev_cla
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{key}/config', cfg)

        # Локальное управление ядром (порт 333) для карточки. Работает только при write_enabled=true;
        # иначе команда отклоняется с уведомлением в temzit/bridge/error. mode=box — значение уходит
        # по Enter, а не на каждое движение ползунка.
        st, cmd = f'{MQTT_PREFIX}/state', f'{MQTT_PREFIX}'
        controls = [
            ('select', 'ctl_mode', 'Темзит Режим работы', {'stat_t': f'{st}/cfg_mode_name', 'cmd_t': f'{cmd}/cmd/set_mode_name',
                                                           'options': list(P1_NAMES.values())}),
            ('number', 'ctl_room_target', 'Темзит Тдома (16 = нет)', {'stat_t': f'{st}/cfg_room_target', 'cmd_t': f'{cmd}/climate/set_temperature',
                                                                     'min': 16, 'max': 30, 'step': 1, 'unit_of_meas': '°C'}),
            ('number', 'ctl_water_target', 'Темзит Тводы', {'stat_t': f'{st}/cfg_water_target', 'cmd_t': f'{cmd}/climate/set_water_temp',
                                                            'min': 5, 'max': 55, 'step': 1, 'unit_of_meas': '°C'}),
            ('number', 'ctl_dhw_target', 'Темзит Тгвс', {'stat_t': f'{st}/cfg_dhw_target', 'cmd_t': f'{cmd}/climate/set_dhw_temp',
                                                         'min': 20, 'max': 70, 'step': 1, 'unit_of_meas': '°C'}),
            ('select', 'ctl_compressor_limit', 'Темзит Лимит ККБ', {'stat_t': f'{st}/cfg_compressor_limit_name',
                                                                    'cmd_t': f'{cmd}/cmd/set_compressor_limit_name',
                                                                    'options': list(COMP_LIMIT_NAMES.values())}),
            ('number', 'ctl_weather_comp', 'Темзит Погодокомпенсация', {'stat_t': f'{st}/cfg_weather_comp', 'cmd_t': f'{cmd}/cmd/set_weather_comp',
                                                                        'min': 0, 'max': 1, 'step': 0.1}),
        ]
        for platform, key, name, extra in controls:
            cfg = {'name': name, 'uniq_id': f'temzit_{key}', 'obj_id': f'temzit_{key}', 'device': device, **avail, **extra}
            if platform == 'number':
                cfg['mode'] = 'box'
            self._discover(platform, f'temzit_{key}', cfg)
        self._flush_discovery()
        self.discovery_sent = True

    @staticmethod
    def _cloud_avty():
        """Облачные сущности доступны, только если работает аддон И включён облачный режим."""
        return {'avty': [{'t': f'{MQTT_PREFIX}/availability'}, {'t': f'{MQTT_PREFIX}/cloud/availability'}],
                'avty_mode': 'all'}

    def publish_cloud_discovery(self):
        device = {'identifiers': ['temzit_hp_1']}
        base = f'{MQTT_PREFIX}/cloud'
        avty = self._cloud_avty()
        for field, name, unit in CLOUD_SENSORS:
            key = f'temzit_cloud_{field.lower()}'
            cfg = {'name': name, 'uniq_id': key, 'obj_id': key, 'device': device, 'stat_t': f'{base}/cfg/{field}', **avty}
            if unit:
                cfg['unit_of_meas'] = unit
            self._discover('sensor', key, cfg)
        for n in range(1, 5):
            key = f'temzit_cloud_schedule_{n}'
            self._discover('sensor', key, {
                'name': f'Темзит Расписание {n}', 'uniq_id': key, 'obj_id': key, 'device': device,
                'stat_t': f'{base}/schedule/{n}', 'json_attr_t': f'{base}/schedule/{n}/attr', **avty})
        self._discover('sensor', 'temzit_cloud_status', {
            'name': 'Темзит Облако', 'uniq_id': 'temzit_cloud_status', 'obj_id': 'temzit_cloud_status',
            'device': device, 'stat_t': f'{base}/status', 'json_attr_t': f'{base}/status/attr'})
        self._discover('switch', 'temzit_cloud_mode', {
            'name': 'Темзит Облачный режим', 'device': device, 'icon': 'mdi:cloud-sync',
            'stat_t': f'{base}/mode', 'cmd_t': f'{base}/mode/set', 'pl_on': 'ON', 'pl_off': 'OFF',
            'availability_topic': f'{MQTT_PREFIX}/availability', 'payload_available': 'online',
            'payload_not_available': 'offline'})
        self._flush_discovery()

    def _load_cloud_mode(self):
        try:
            with open(os.path.join(self.backup_dir, 'cloud_mode.json'), encoding='utf-8') as f:
                return bool(json.load(f).get('cloud_on', True))
        except Exception:
            return True

    def _publish_cloud_mode(self):
        base = f'{MQTT_PREFIX}/cloud'
        self.publish(f'{base}/mode', 'ON' if self.cloud_on else 'OFF')
        self.publish(f'{base}/availability', 'online' if self.cloud_on else 'offline')
        if not self.cloud_on:
            self.publish(f'{base}/status', 'off')
            self.publish(f'{base}/status/attr', {'mode': 'local', 'write_enabled': CLOUD_WRITE})

    def _set_cloud_mode(self, payload):
        on = payload.strip().upper() in ('ON', '1', 'TRUE')
        changed = on != self.cloud_on
        self.cloud_on = on
        if not on:
            with self._cloud_lock:    # несохранённые облачные изменения отбрасываем
                if self._cloud_timer:
                    self._cloud_timer.cancel()
                self._cloud_pending, self._cloud_timer = {}, None
        if changed and self.backup_dir:
            try:
                with open(os.path.join(self.backup_dir, 'cloud_mode.json'), 'w', encoding='utf-8') as f:
                    json.dump({'cloud_on': on}, f)
            except Exception as e:
                print(f'Cloud mode: не удалось сохранить выбор: {e}', flush=True)
        self._publish_cloud_mode()
        if changed:
            print(f'Cloud mode: {"облачный" if on else "локальный"}', flush=True)
        if on:
            self._cloud_wake.set()    # сразу перечитать облако

    def publish_cloud_write_discovery(self, fields, rows):
        """Управляемые сущности для записи через облако (категория «Настройки» на странице устройства).
        Публикуются после первого чтения: диапазоны и варианты берутся прямо из формы сервера."""
        device = {'identifiers': ['temzit_hp_1']}
        base = f'{MQTT_PREFIX}/cloud'
        avty = self._cloud_avty()
        for field, name in CLOUD_WRITE_NUMBERS:
            nums = sorted(int(v) for v in fields[field]['options'])
            key = f'temzit_cloud_set_{field.lower()}'
            self._discover('number', key, {
                'name': name, 'uniq_id': key, 'obj_id': key, 'device': device, 'ent_cat': 'config',
                'stat_t': f'{base}/cfg/{field}', 'cmd_t': f'{base}/set/cfg/{field}', **avty,
                'min': nums[0], 'max': nums[-1], 'step': 1, 'mode': 'box', 'unit_of_meas': '°C'})
        for field, name in CLOUD_WRITE_SWITCHES:
            key = f'temzit_cloud_set_{field.lower()}'
            self._discover('switch', key, {
                'name': name, 'uniq_id': key, 'obj_id': key, 'device': device, 'ent_cat': 'config',
                'stat_t': f'{base}/cfg/{field}', 'cmd_t': f'{base}/set/cfg/{field}', **avty,
                'pl_on': '1', 'pl_off': '0', 'stat_on': 'Да', 'stat_off': 'Нет'})
        for r in rows:
            n = r['row']
            for k, label in CLOUD_SCHEDULE_LABELS:
                opts = list(r['options'][k].values())
                if len(set(opts)) != len(opts):
                    print(f'Cloud: у поля расписания {n}/{k} неуникальные подписи — сущность не создана', flush=True)
                    continue
                key = f'temzit_cloud_sched{n}_{k}'
                self._discover('select', key, {
                    'name': f'Темзит Расписание {n}: {label}', 'uniq_id': key, 'obj_id': key, 'device': device,
                    'ent_cat': 'config', 'stat_t': f'{base}/schedule/{n}/{k}',
                    'cmd_t': f'{base}/set/schedule/{n}/{k}', 'options': opts, **avty})
        self._flush_discovery()

    def _cloud_poll(self):
        base = f'{MQTT_PREFIX}/cloud'
        fields = temzit_cloud.get_config(CLOUD_LOGIN, CLOUD_SERIAL, CLOUD_PASS)
        rows = temzit_cloud.get_schedule(CLOUD_LOGIN, CLOUD_SERIAL, CLOUD_PASS)
        units = {f: u for f, _, u in CLOUD_SENSORS}
        for name, f in fields.items():
            self.publish(f'{base}/cfg/{name}', f['value'] if units.get(name) else temzit_cloud.label_of(f))
        self.publish(f'{base}/cfg/json', {n: {'value': f['value'], 'label': temzit_cloud.label_of(f)} for n, f in fields.items()})
        for r in rows:
            n = r['row']
            self.publish(f'{base}/schedule/{n}', r['summary'])
            self.publish(f'{base}/schedule/{n}/attr', {**r['labels'], 'raw': r['raw']})
            for k, _ in CLOUD_SCHEDULE_LABELS:
                self.publish(f'{base}/schedule/{n}/{k}', r['labels'][k])
                self._cloud_sched_options[(n, k)] = r['options'][k]
        if CLOUD_WRITE and not self._cloud_write_discovered:
            self.publish_cloud_write_discovery(fields, rows)
            self._cloud_write_discovered = True
        return len(fields), len(rows)

    def _cloud_refresh(self):
        """Опрос облака с публикацией статуса; сетевые обращения сериализованы с записью."""
        if not self.cloud_on:
            return
        base = f'{MQTT_PREFIX}/cloud'
        now = datetime.datetime.now().isoformat(timespec='seconds')
        with self._cloud_io:
            try:
                nf, nr = self._cloud_poll()
                self.publish(f'{base}/status', 'ok')
                self.publish(f'{base}/status/attr', {'updated': now, 'fields': nf, 'schedule_rows': nr,
                                                     'interval_s': CLOUD_INTERVAL, 'write_enabled': CLOUD_WRITE})
                print(f'Cloud: OK ({nf} полей настроек, {nr} строки расписания)', flush=True)
            except Exception as e:
                # temzit_cloud не включает URL/пароль в тексты ошибок
                self.publish(f'{base}/status', 'error')
                self.publish(f'{base}/status/attr', {'error': str(e), 'failed_at': now, 'interval_s': CLOUD_INTERVAL,
                                                     'write_enabled': CLOUD_WRITE})
                print(f'Cloud ERROR: {e}', flush=True)

    def _cloud_loop(self):
        while True:
            self._cloud_refresh()          # в локальном режиме сразу выходит
            self._cloud_wake.wait(CLOUD_INTERVAL)
            self._cloud_wake.clear()

    def _handle_cloud_cmd(self, topic, payload):
        if not CLOUD_WRITE:
            raise ValueError('запись через облако выключена (cloud_write_enabled=false)')
        if not self.cloud_on:
            raise ValueError('включён локальный режим — облачные настройки не меняются')
        parts = topic[len(f'{MQTT_PREFIX}/cloud/set/'):].split('/')
        if parts[0] == 'cfg' and len(parts) == 2:
            field = parts[1]
            if field not in temzit_cloud.WRITABLE_CFG:
                raise ValueError(f'поле {field} нельзя менять из HA')
            if field in dict(CLOUD_WRITE_NUMBERS):
                value = str(int(round(float(payload))))
            else:
                value = '1' if payload.lower() in ('1', 'on', 'true', 'да') else '0'
            self._cloud_queue('cfg', field, value)
        elif parts[0] == 'schedule' and len(parts) == 3:
            row, key = int(parts[1]), parts[2]
            opts = self._cloud_sched_options.get((row, key))
            if not opts:
                raise ValueError('расписание ещё не прочитано из облака')
            by_label = {lbl: v for v, lbl in opts.items()}
            value = by_label.get(payload, payload if payload in opts else None)
            if value is None:
                raise ValueError(f'недопустимое значение {payload!r} для расписания {row}/{key}')
            self._cloud_queue(row, key, value)
        else:
            raise ValueError(f'неизвестная команда облака: {topic}')

    def _cloud_queue(self, target, key, value):
        """Копим изменения CLOUD_WRITE_DEBOUNCE секунд, чтобы движение ползунка дало одну запись."""
        with self._cloud_lock:
            self._cloud_pending.setdefault(target, {})[key] = value
            if self._cloud_timer:
                self._cloud_timer.cancel()
            self._cloud_timer = threading.Timer(CLOUD_WRITE_DEBOUNCE, self._cloud_flush)
            self._cloud_timer.daemon = True
            self._cloud_timer.start()

    def _cloud_backup(self, target, before, changes):
        """Бэкап полной формы перед отправкой в облако. Нет бэкапа -> нет записи."""
        if not self.backup_dir:
            self.backup_dir = resolve_writable_dir(TEMZIT_DATA_DIR)
        if not self.backup_dir:
            raise RuntimeError('нет записываемого каталога для бэкапа')
        ts = datetime.datetime.now()
        what = 'cfg' if target == 'cfg' else f'schedule{target}'
        rec = {'ts': ts.isoformat(timespec='seconds'), 'reason': f'pre_cloud_write_{what}', 'version': VERSION,
               'target': what, 'changes': changes, 'form': before}
        path = os.path.join(self.backup_dir, f'cloud_{what}_{ts.strftime("%Y%m%d_%H%M%S_%f")}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        with open(os.path.join(self.backup_dir, 'temzit_cloud_history.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print(f'Cloud backup written: {path}', flush=True)
        return path

    def _cloud_flush(self):
        with self._cloud_lock:
            pending, self._cloud_pending, self._cloud_timer = self._cloud_pending, {}, None
        if not self.cloud_on:
            return
        base = f'{MQTT_PREFIX}/cloud'
        written = False
        with self._cloud_io:
            for target, changes in pending.items():
                now = datetime.datetime.now().isoformat(timespec='seconds')
                what = 'настройки' if target == 'cfg' else f'расписание {target}'
                backup = lambda before, t=target, c=changes: self._cloud_backup(t, before, c)
                try:
                    if target == 'cfg':
                        applied, status = temzit_cloud.set_config(changes, CLOUD_LOGIN, CLOUD_SERIAL, CLOUD_PASS, before_post=backup)
                    else:
                        applied, status = temzit_cloud.set_schedule(target, changes, CLOUD_LOGIN, CLOUD_SERIAL, CLOUD_PASS, before_post=backup)
                    written = written or bool(applied)
                    print(f'Cloud write ({what}): {applied or changes} -> {status}', flush=True)
                    self.publish(f'{base}/write/last', {'ts': now, 'target': str(target), 'requested': changes,
                                                        'applied': applied, 'status': status}, retain=False)
                except Exception as e:
                    print(f'Cloud write ERROR ({what}): {e}', flush=True)
                    self.publish(f'{base}/write/last', {'ts': now, 'target': str(target), 'requested': changes,
                                                        'status': 'error', 'error': str(e)}, retain=False)
                    self.publish(f'{MQTT_PREFIX}/bridge/error', {'cloud_write_error': str(e), 'target': str(target)}, retain=False)
        self._cloud_refresh()
        if written:
            t = threading.Timer(CLOUD_RECHECK_AFTER, self._cloud_refresh)
            t.daemon = True
            t.start()

    def _publish_state(self, state: dict):
        mode_code = state.get('mode_code')
        state['ha_mode'] = MODE_CODE_TO_HA.get(mode_code, 'off')
        state['climate_target_temp'] = state.get('set_room') or state.get('cfg_room_target')
        if state.get('compressor_active') is None:
            state['compressor_active'] = 'OFF'
        state['dhw_ha_mode'] = 'heat' if state['ha_mode'] != 'off' else 'off'
        sync_raw = state.get('_sync_raw')
        if sync_raw is not None:
            self._last_sync_raw = sync_raw
            self.publish(f'{MQTT_PREFIX}/sync/raw', sync_raw, retain=False)
        self.publish(f'{MQTT_PREFIX}/state/json', state)
        for k, v in state.items():
            if v is not None and not k.startswith('_'):
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v) if not isinstance(v, (dict, list)) else json.dumps(v))

    def _publish_cfg(self, cfg: dict):
        raw = cfg.get('_raw')
        self._last_cfg_raw = raw
        ok, reason = looks_like_valid_cfg(raw)
        if ok:
            self._last_good_cfg_raw = list(raw)
        self.publish(f'{MQTT_PREFIX}/diag/set_guard', {'status': 'cfg_seen', 'valid': ok, 'reason': reason, 'cfg_raw': raw, 'last_good_cfg_raw': self._last_good_cfg_raw}, retain=False)
        self.publish(f'{MQTT_PREFIX}/cfg/json', {k: v for k, v in cfg.items() if k != '_raw'})
        self.publish(f'{MQTT_PREFIX}/cfg/raw', raw)
        for k, v in cfg.items():
            if v is not None and k != '_raw':
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v) if not isinstance(v, (dict, list)) else json.dumps(v))

    def maybe_poll_cfg(self):
        if TEMZIT_CFG_INTERVAL <= 0:
            return
        now = time.time()
        if now - self.last_cfg_poll < TEMZIT_CFG_INTERVAL:
            return
        wait = self.last_sync_ts + TEMZIT_CFG_DELAY_AFTER_SYNC - now
        if wait > 0:
            time.sleep(wait)
        try:
            cfg = self.temzit.get_cfg()
            self._publish_cfg(cfg)
            self._flush_pending_set()
            self.last_cfg_poll = time.time()
        except Exception as ce:
            print(f'CFG ERROR: {ce}', flush=True)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'cfg_error': str(ce)})

    def loop(self):
        # Понятная диагностика вместо тёмной ошибки сокета, если параметры не заполнены.
        # При host_network=true имя 'core-mosquitto' может не резолвиться — нужен IP брокера.
        if not MQTT_HOST:
            print('FATAL: mqtt_host не задан. Укажите IP MQTT-брокера в настройках аддона '
                  "(при host_network имя 'core-mosquitto' может не резолвиться).", flush=True)
            raise SystemExit(1)
        if not TEMZIT_HOST:
            print('FATAL: temzit_host не задан. Укажите IP гидромодуля ТЭМЗИТ (порт 333).', flush=True)
            raise SystemExit(1)
        print(f'Connecting to MQTT {MQTT_HOST}:{MQTT_PORT}, Temzit {TEMZIT_HOST}:{TEMZIT_PORT}', flush=True)
        print(f'WRITE_ENABLED={WRITE_ENABLED} (запись {"РАЗРЕШЕНА" if WRITE_ENABLED else "ВЫКЛЮЧЕНА — кадр 0x35 не подтверждён"})', flush=True)
        self.client.will_set(f'{MQTT_PREFIX}/availability', 'offline', retain=True)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        self.publish_discovery()
        if CLOUD_ENABLED:
            print(f'Cloud: включён ({"чтение и запись" if CLOUD_WRITE else "только чтение"}), логин {CLOUD_LOGIN}, '
                  f'опрос раз в {CLOUD_INTERVAL}с', flush=True)
            self.publish_cloud_discovery()
            self._publish_cloud_mode()
            print(f'Cloud mode: {"облачный" if self.cloud_on else "локальный"}', flush=True)
            threading.Thread(target=self._cloud_loop, daemon=True).start()
        else:
            print('Cloud: выключен (cloud_login/cloud_serial/cloud_password не заданы)', flush=True)
        while True:
            try:
                state = self.temzit.get_sync()
                self.last_sync_ts = time.time()
                self.publish(f'{MQTT_PREFIX}/availability', 'online')
                self._publish_state(state)
            except Exception as e:
                self.publish(f'{MQTT_PREFIX}/availability', 'degraded')
                self.publish(f'{MQTT_PREFIX}/bridge/error', {'sync_error': str(e)})
                time.sleep(TEMZIT_RETRY_DELAY)
                continue
            try:
                self.maybe_poll_cfg()
            except Exception as ce:
                self.publish(f'{MQTT_PREFIX}/bridge/error', {'cfg_error': str(ce)})
            time.sleep(TEMZIT_SYNC_INTERVAL)


if __name__ == '__main__':
    Bridge().loop()

#!/usr/bin/env python3
"""
Temzit MQTT Bridge v0.5.1
Что изменилось vs v0.5.0:
- get_sync(): срез p = data[2:62], смещения set_room=p[47], set_water=p[49],
  hour=p[57], minute=p[58], second=p[59] (исправлены)
- get_cfg():  срез p = data[3:33], cfg_water_target=p[1], cfg_dhw_target=p[6],
  cfg_boiler_mode=p[7], cfg_compressor_limit=p[8], cfg_weather_comp=p[17],
  cfg_dhw_max=p[20], cfg_flowmeter=p[21], cfg_backup=p[23]
  cfg_ten_on_outdoor и cfg_kkb_off_outdoor — знаковый int8 через s8()
- set_cfg():  смещения записи: water=1, dhw=6, comp_limit=8
"""
import os, time, json, socket, threading
import paho.mqtt.client as mqtt

TEMZIT_HOST               = os.getenv('TEMZIT_HOST', '192.168.2.20')
TEMZIT_PORT               = int(os.getenv('TEMZIT_PORT', '333'))
TEMZIT_TIMEOUT            = int(os.getenv('TEMZIT_TIMEOUT', '15'))
TEMZIT_SYNC_INTERVAL      = int(os.getenv('TEMZIT_SYNC_INTERVAL', '60'))
TEMZIT_CFG_INTERVAL       = int(os.getenv('TEMZIT_CFG_INTERVAL', '900'))
TEMZIT_CFG_DELAY_AFTER_SYNC = int(os.getenv('TEMZIT_CFG_DELAY_AFTER_SYNC', '12'))
TEMZIT_RETRY_DELAY        = int(os.getenv('TEMZIT_RETRY_DELAY', '15'))
MQTT_HOST                 = os.getenv('MQTT_HOST', '192.168.1.50')
MQTT_PORT                 = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER                 = os.getenv('MQTT_USER', '')
MQTT_PASS                 = os.getenv('MQTT_PASS', '')
MQTT_PREFIX               = os.getenv('MQTT_PREFIX', 'temzit')
MQTT_DISCOVERY_PREFIX     = os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant')
MQTT_CLIENT_ID            = os.getenv('MQTT_CLIENT_ID', 'temzit-bridge')

VERSION = "0.5.1"

CMD_SYNC    = 0x30
CMD_REQCFG  = 0x34
CMD_SETCFG  = 0x35
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

MODE_CODE_TO_HA = {
    0: 'off',
    1: 'heat',
    2: 'heat',  # Быстрый нагрев
    3: 'heat',  # Только ТЭНы
    4: 'cool',
    5: 'heat',  # Внешний
}

HA_MODE_TO_P1 = {
    'off':  0,
    'heat': 1,
    'cool': 4,
}

HA_MODES = ['off', 'heat', 'cool']

P1_NAMES = {
    0: 'Стоп',
    1: 'Нагрев',
    2: 'Быстрый',
    3: 'ТЭН',
    4: 'Холод',
    5: 'Внешний',
}

DHW_MODE_NAMES = {
    0:  'Выключен',
    1:  'Только ТЭН в баке',
    2:  'ТН 10%',
    3:  'ТН 20%',
    4:  'ТН 30%',
    5:  'ТН 40%',
    6:  'ТН 50%',
    7:  'ТН 60%',
    8:  'ТН 70%',
    9:  'ТН 80%',
    10: 'ТН 90%',
    11: 'ТН 100%',
}

COMP_LIMIT_NAMES = {
    0:  'Без ограничений',
    1:  '10%',
    2:  '20%',
    3:  '30%',
    4:  '40%',
    5:  '50%',
    6:  '55%',
    7:  '60%',
    8:  '70%',
    9:  '80%',
    10: '90%',
}

BACKUP_TYPE_NAMES = {
    0: 'Не использовать',
    1: 'после I ступени',
    2: 'после II ступени',
    3: 'после III ступени',
    4: 'только внешний',
}

FLOWMETER_TYPES = {
    0: 'unknown',
    1: 'impulse_1l',
    2: 'impulse_10l',
    3: 'dual_channel',
    4: 'electronic',
    5: 'fixed',
    6: 'reed_switch',
}

ALARM_BITS = {
    0x0001: 'contactor_fail_e05',
    0x0002: 'link_fail_e08',
    0x0004: 'flow1_fail_e01',
    0x0008: 'wifiterm_fail_e07',
    0x0010: 'tevap_fail_e09',
    0x0020: 'tcompover_fail_e02',
    0x0040: 'tevaplow_fail_e03',
    0x0080: 'kkb1_fail_e04',
    0x0100: 'clock_fail_e06',
    0x0200: 'wifi_error_e0A',
    0x4000: 'crit_tsens_fail',
    0x8000: 'lcdversion_fail',
}


def s8(v):
    """Беззнаковый byte → знаковый int8."""
    if v is None:
        return None
    return v if v < 128 else v - 256


def weather_comp_from_raw(v):
    if v is None:
        return None
    return round(v * 0.1, 1)


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def u16le(buf: bytes, off: int):
    if len(buf) < off + 2:
        return None
    return int.from_bytes(buf[off:off+2], 'little', signed=False)


def heater_stage_from_raw(v):
    if v is None:
        return None
    if 9  <= v <= 84:  return 1
    if 85 <= v <= 169: return 2
    if 170 <= v <= 255: return 3
    return 0


def dhw_heater_on(v):
    if v is None:
        return None
    return 'ON' if (v & 0x1) else 'OFF'


def decode_alarm(v):
    if v is None:
        return None
    names = [name for bit, name in ALARM_BITS.items() if v & bit]
    return 'ok' if not names else ','.join(names)


def build_setcfg(cfg_bytes: list) -> bytes:
    payload = bytes([CMD_SETCFG, len(cfg_bytes)] + cfg_bytes)
    crc = checksum16(payload)
    return payload + crc.to_bytes(2, 'little')


# ── TCP-клиент ──────────────────────────────────────────────────────────────
class TemzitClient:
    def __init__(self, host, port, timeout):
        self.host    = host
        self.port    = port
        self.timeout = timeout
        self.lock    = threading.Lock()

    def _query(self, payload: bytes) -> tuple:
        with self.lock:
            t0 = time.time()
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(payload)
                data = s.recv(256)
            dt = round((time.time() - t0) * 1000)
            return data, dt

    def get_sync(self) -> dict:
        data, dt = self._query(bytes([CMD_SYNC, 0x00]))
        if len(data) < 4:
            raise TimeoutError(f'sync short reply: {len(data)} bytes')
        if data[0] != RESP_ACTUAL:
            raise ValueError(f'unexpected sync reply type: {data[0]}')
        if len(data) < 64:
            raise ValueError(f'incomplete sync reply: {len(data)} bytes')
        crc_rx   = int.from_bytes(data[62:64], 'little')
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'sync CRC mismatch: rx={crc_rx} calc={crc_calc}')

        p = data[2:62]  # v0.5.1: правильный срез

        def t(off):
            v = u16le(p, off)
            return None if v is None else v / 10.0

        flow_raw              = u16le(p, 18)
        heater_state          = u16le(p, 24)
        dhw_state             = u16le(p, 26)
        alarm                 = u16le(p, 30)
        compressor_hz_1       = p[22] if len(p) > 22 else None
        compressor_hz_2       = p[23] if len(p) > 23 else None
        mode_code             = u16le(p, 0)
        set_compressor_limit_raw = p[52] if len(p) > 52 else None

        return {
            'diag_sync_len':    len(data),
            'diag_sync_ms':     dt,
            'mode_code':        mode_code,
            'mode_name':        P1_NAMES.get(mode_code, f'mode_{mode_code}'),
            'schedule_no':      u16le(p, 2),
            't_outdoor':        t(4),
            't_room':           t(6),
            't_supply':         t(8),
            't_return':         t(10),
            't_freon_gas':      t(12),
            't_freon_liquid':   t(14),
            't_dhw':            t(16),
            'flow_raw':         flow_raw,
            'flow_l_min':       None if flow_raw is None else flow_raw * 4,
            'compressor_type':  p[20] if len(p) > 20 else None,
            'compressor_active':p[21] if len(p) > 21 else None,
            'compressor_hz_1':  compressor_hz_1,
            'compressor_hz_2':  compressor_hz_2,
            'heater_state_raw': heater_state,
            'heater_stage':     heater_stage_from_raw(heater_state),
            'dhw_heater_state_raw': dhw_state,
            'dhw_heater_on':    dhw_heater_on(dhw_state),
            'power_kw':         None if u16le(p, 28) is None else u16le(p, 28) / 100.0,
            'alarm':            alarm,
            'alarm_text':       decode_alarm(alarm),
            'active_schedule_no':   p[45] if len(p) > 45 else None,
            'active_schedule_mode': p[46] if len(p) > 46 else None,
            'set_room':         p[47] if len(p) > 47 else None,   # v0.5.1
            'set_water':        p[49] if len(p) > 49 else None,   # v0.5.1
            'set_dhw':          p[51] if len(p) > 51 else None,
            'set_compressor_limit':      set_compressor_limit_raw,
            'set_compressor_limit_name': COMP_LIMIT_NAMES.get(set_compressor_limit_raw,
                                                               str(set_compressor_limit_raw)),
            'set_ten_mode':     p[53] if len(p) > 53 else None,
            'set_dhw_mode':     p[54] if len(p) > 54 else None,
            'set_dhw_mode_name':DHW_MODE_NAMES.get(p[54] if len(p) > 54 else None, '?'),
            'weekday':          p[56] if len(p) > 56 else None,
            'hour':             p[57] if len(p) > 57 else None,   # v0.5.1
            'minute':           p[58] if len(p) > 58 else None,   # v0.5.1
            'second':           p[59] if len(p) > 59 else None,   # v0.5.1
        }

    def get_cfg(self) -> dict:
        data, dt = self._query(bytes([CMD_REQCFG, 0x00]))
        if len(data) < 4:
            raise TimeoutError(f'cfg short reply: {len(data)} bytes')
        if data[0] != RESP_CONFIG:
            raise ValueError(f'unexpected cfg reply type: {data[0]}')
        if len(data) < 64:
            raise ValueError(f'incomplete cfg reply: {len(data)} bytes')
        crc_rx   = int.from_bytes(data[62:64], 'little')
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'cfg CRC mismatch: rx={crc_rx} calc={crc_calc}')

        p = data[3:33]  # v0.5.1: правильный срез (пропускаем тип, длину, флаг)

        flowmeter_type  = p[21] if len(p) > 21 else None  # v0.5.1
        boiler_mode_raw = p[7]  if len(p) > 7  else None  # v0.5.1
        comp_limit_raw  = p[8]  if len(p) > 8  else None  # v0.5.1
        weather_raw     = p[17] if len(p) > 17 else None  # v0.5.1
        backup_raw      = p[23] if len(p) > 23 else None  # v0.5.1

        return {
            'diag_cfg_len':    len(data),
            'diag_cfg_ms':     dt,
            'cfg_mode':        p[0] if len(p) > 0 else None,
            'cfg_room_target': p[1] if len(p) > 1 else None,
            'cfg_water_target':p[1] if len(p) > 1 else None,  # v0.5.1: offset 1
            'cfg_ten_on_outdoor':  s8(p[3] if len(p) > 3 else None),   # v0.5.1: знаковый
            'cfg_kkb_off_outdoor': s8(p[4] if len(p) > 4 else None),   # v0.5.1: знаковый
            'cfg_p5':          p[5] if len(p) > 5 else None,
            'cfg_p6':          p[6] if len(p) > 6 else None,
            'cfg_dhw_target':  p[6] if len(p) > 6 else None,  # v0.5.1: offset 6
            'cfg_boiler_mode': boiler_mode_raw,
            'cfg_boiler_mode_name': DHW_MODE_NAMES.get(boiler_mode_raw, str(boiler_mode_raw)),
            'cfg_compressor_limit':      comp_limit_raw,
            'cfg_compressor_limit_name': COMP_LIMIT_NAMES.get(comp_limit_raw, str(comp_limit_raw)),
            'cfg_weather_comp':      weather_raw,
            'cfg_weather_comp_val':  weather_comp_from_raw(weather_raw),
            'cfg_dhw_max_from_compressor': p[20] if len(p) > 20 else None,  # v0.5.1
            'cfg_flowmeter_type':      flowmeter_type,
            'cfg_flowmeter_type_name': FLOWMETER_TYPES.get(flowmeter_type,
                                                            f'unknown_{flowmeter_type}'),
            'cfg_backup_type':      backup_raw,
            'cfg_backup_type_name': BACKUP_TYPE_NAMES.get(backup_raw, str(backup_raw)),
            '_raw': list(p),
        }

    def set_cfg(self, cfg_raw: list, updates: dict) -> None:
        new_cfg = list(cfg_raw)
        for offset, value in updates.items():
            if offset < len(new_cfg):
                new_cfg[offset] = int(value)
        packet = build_setcfg(new_cfg)
        with self.lock:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(packet)
                try:
                    s.recv(64)
                except Exception:
                    pass


# ── MQTT Bridge ──────────────────────────────────────────────────────────────
class Bridge:
    def __init__(self):
        self.temzit  = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client  = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.discovery_sent   = False
        self.last_cfg_poll    = 0
        self.last_sync_ts     = 0
        self._last_cfg_raw    = None
        self._pending_set     = {}
        self._set_lock        = threading.Lock()

    def publish(self, topic, payload, retain=True, qos=0):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_mode')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_temperature')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_water_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_dhw_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_compressor_limit')
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_byte')

    def on_message(self, client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        try:
            self._handle_cmd(topic, payload)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error',
                         {'cmd_error': str(e), 'topic': topic, 'payload': payload})

    def _handle_cmd(self, topic, payload):
        suffix = topic.split('/')[-1]

        if suffix == 'set_mode':
            p1 = HA_MODE_TO_P1.get(payload.lower())
            if p1 is None:
                raise ValueError(f'Unknown mode: {payload}')
            self._queue_set(0, p1)

        elif suffix == 'set_temperature':
            v = max(16, min(30, round(float(payload))))
            self._queue_set(1, v)

        elif suffix == 'set_water_temp':
            v = max(5, min(55, round(float(payload))))
            self._queue_set(1, v)   # v0.5.1: offset 1

        elif suffix == 'set_dhw_temp':
            v = max(20, min(70, round(float(payload))))
            self._queue_set(6, v)   # v0.5.1: offset 6

        elif suffix == 'set_compressor_limit':
            v = max(0, min(10, round(float(payload))))
            self._queue_set(8, v)   # v0.5.1: offset 8

        elif suffix == 'set_byte':
            data = json.loads(payload)
            self._queue_set(int(data['offset']), int(data['value']))

    def _queue_set(self, offset: int, value: int):
        with self._set_lock:
            self._pending_set[offset] = value
        self._flush_pending_set()

    def _flush_pending_set(self):
        with self._set_lock:
            if not self._pending_set:
                return
            if self._last_cfg_raw is None:
                return
            updates = dict(self._pending_set)
            self._pending_set.clear()
        self.temzit.set_cfg(self._last_cfg_raw, updates)
        threading.Timer(2.0, self._force_sync).start()

    def _force_sync(self):
        try:
            state = self.temzit.get_sync()
            self._publish_state(state)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'force_sync_error': str(e)})

    # ── Discovery ──────────────────────────────────────────────────────────
    def publish_discovery(self):
        if self.discovery_sent:
            return
        device = {
            'identifiers':  ['temzit_hp_1'],
            'name':         'Temzit Heat Pump',
            'manufacturer': 'ТЭМЗИТ',
            'model':        'Hydromodule',
            'sw_version':   VERSION,
        }
        avail = {
            'availability_topic':    f'{MQTT_PREFIX}/availability',
            'payload_available':     'online',
            'payload_not_available': 'offline',
        }

        # CLIMATE — отопление
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_climate/config', {
            'name': 'Temzit', 'uniq_id': 'temzit_climate',
            'device': device, **avail,
            'curr_temp_t':  f'{MQTT_PREFIX}/state/t_room',
            'temp_stat_t':  f'{MQTT_PREFIX}/state/climate_target_temp',
            'temp_cmd_t':   f'{MQTT_PREFIX}/climate/set_temperature',
            'temp_step': 1, 'min_temp': 16, 'max_temp': 30,
            'mode_stat_t':  f'{MQTT_PREFIX}/state/ha_mode',
            'mode_cmd_t':   f'{MQTT_PREFIX}/climate/set_mode',
            'modes': HA_MODES,
            'precision': 0.1,
        })

        # CLIMATE — ГВС
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_dhw_climate/config', {
            'name': 'Temzit ГВС', 'uniq_id': 'temzit_dhw_climate',
            'device': device, **avail,
            'curr_temp_t':  f'{MQTT_PREFIX}/state/t_dhw',
            'temp_stat_t':  f'{MQTT_PREFIX}/state/cfg_dhw_target',
            'temp_cmd_t':   f'{MQTT_PREFIX}/climate/set_dhw_temp',
            'temp_step': 1, 'min_temp': 20, 'max_temp': 70,
            'mode_stat_t':  f'{MQTT_PREFIX}/state/dhw_ha_mode',
            'mode_cmd_t':   f'{MQTT_PREFIX}/climate/set_mode',
            'modes': ['off', 'heat'],
            'precision': 1,
        })

        # SENSORS
        sensors = [
            ('outdoor_temperature',         'Температура улица',            't_outdoor',                'temperature', '°C'),
            ('room_temperature',            'Температура дом',              't_room',                   'temperature', '°C'),
            ('supply_temperature',          'Температура подачи',           't_supply',                 'temperature', '°C'),
            ('return_temperature',          'Температура обратки',          't_return',                 'temperature', '°C'),
            ('dhw_temperature',             'Температура ГВС',              't_dhw',                    'temperature', '°C'),
            ('freon_gas_temperature',       'Фреон газ',                    't_freon_gas',              'temperature', '°C'),
            ('freon_liquid_temperature',    'Фреон жидкость',               't_freon_liquid',           'temperature', '°C'),
            ('power_kw',                    'Мощность',                     'power_kw',                 'power',       'kW'),
            ('flow_l_min',                  'Проток',                       'flow_l_min',               None,          'L/min'),
            ('compressor_hz_1',             'ККБ1 частота',                 'compressor_hz_1',          'frequency',   'Hz'),
            ('compressor_hz_2',             'ККБ2 частота',                 'compressor_hz_2',          'frequency',   'Hz'),
            ('heater_stage',                'Ступень ТЭНа',                 'heater_stage',             None,          None),
            ('mode_name',                   'Режим работы',                 'mode_name',                None,          None),
            ('set_water',                   'Уставка t воды',               'set_water',                'temperature', '°C'),
            ('set_dhw_mode_name',           'Режим ГВС',                    'set_dhw_mode_name',        None,          None),
            ('set_compressor_limit_name',   'Лимит компрессора',            'set_compressor_limit_name',None,          None),
            ('cfg_water_target',            'Конфиг t воды',                'cfg_water_target',         'temperature', '°C'),
            ('cfg_dhw_target',              'Конфиг t ГВС',                 'cfg_dhw_target',           'temperature', '°C'),
            ('cfg_boiler_mode_name',        'Режим ГВС (конфиг)',           'cfg_boiler_mode_name',     None,          None),
            ('cfg_compressor_limit_name',   'Лимит компрессора (конфиг)',   'cfg_compressor_limit_name',None,          None),
            ('cfg_weather_comp_val',        'Погодная компенсация',         'cfg_weather_comp_val',     None,          None),
            ('cfg_ten_on_outdoor',          'Т включения ТЭНа',             'cfg_ten_on_outdoor',       'temperature', '°C'),
            ('cfg_kkb_off_outdoor',         'Т выкл. компрессора',          'cfg_kkb_off_outdoor',      'temperature', '°C'),
            ('cfg_dhw_max_from_compressor', 'Макс t ГВС от ТН',            'cfg_dhw_max_from_compressor','temperature','°C'),
            ('cfg_backup_type_name',        'Внешний нагреватель',          'cfg_backup_type_name',     None,          None),
            ('cfg_flowmeter_type_name',     'Датчик протока',               'cfg_flowmeter_type_name',  None,          None),
            ('diag_sync_ms',                'Ping SYNC (мс)',               'diag_sync_ms',             None,          'ms'),
            ('diag_cfg_ms',                 'Ping CFG (мс)',                'diag_cfg_ms',              None,          'ms'),
        ]
        for object_id, name, field, devcls, unit in sensors:
            cfg = {
                'name': name, 'uniq_id': f'temzit_{object_id}',
                'stat_t': f'{MQTT_PREFIX}/state/{field}',
                'device': device, **avail,
            }
            if devcls: cfg['dev_cla'] = devcls
            if unit:   cfg['unit_of_meas'] = unit
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{object_id}/config', cfg)

        # BINARY SENSORS
        for object_id, name, field in [
            ('dhw_heater_on',    'ТЭН БКН',           'dhw_heater_on'),
            ('compressor_active','Компрессор активен', 'compressor_active'),
        ]:
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/binary_sensor/temzit_{object_id}/config', {
                'name': name, 'uniq_id': f'temzit_{object_id}',
                'stat_t': f'{MQTT_PREFIX}/state/{field}',
                'payload_on': 'ON', 'payload_off': 'OFF',
                'device': device, **avail,
            })

        # ALARM
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_alarm_text/config', {
            'name': 'Авария', 'uniq_id': 'temzit_alarm_text',
            'stat_t': f'{MQTT_PREFIX}/state/alarm_text',
            'device': device, **avail,
        })

        # NUMBER entities
        numbers = [
            ('water_temp_target', 'Уставка t воды',
             f'{MQTT_PREFIX}/state/cfg_water_target',
             f'{MQTT_PREFIX}/climate/set_water_temp', 5, 55, 1, '°C'),
            ('dhw_temp_target', 'Уставка t ГВС',
             f'{MQTT_PREFIX}/state/cfg_dhw_target',
             f'{MQTT_PREFIX}/climate/set_dhw_temp', 20, 70, 1, '°C'),
        ]
        for obj_id, name, stat_t, cmd_t, mn, mx, step, unit in numbers:
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/number/temzit_{obj_id}/config', {
                'name': name, 'uniq_id': f'temzit_{obj_id}',
                'stat_t': stat_t, 'cmd_t': cmd_t,
                'min': mn, 'max': mx, 'step': step,
                'unit_of_meas': unit, 'mode': 'box',
                'device': device, **avail,
            })

        self.discovery_sent = True

    # ── State publishing ─────────────────────────────────────────────────────
    def _publish_state(self, state: dict):
        mode_code = state.get('mode_code')
        state['ha_mode']             = MODE_CODE_TO_HA.get(mode_code, 'off')
        state['climate_target_temp'] = state.get('set_room') or state.get('cfg_room_target')
        ca = state.get('compressor_active')
        state['compressor_active']   = 'ON' if ca else 'OFF'
        state['dhw_ha_mode']         = 'heat' if state['ha_mode'] != 'off' else 'off'

        self.publish(f'{MQTT_PREFIX}/state/json', state)
        for k, v in state.items():
            if v is not None and not k.startswith('_'):
                self.publish(f'{MQTT_PREFIX}/state/{k}',
                             str(v) if not isinstance(v, (dict, list)) else json.dumps(v))

    def _publish_cfg(self, cfg: dict):
        self._last_cfg_raw = cfg.get('_raw')
        self.publish(f'{MQTT_PREFIX}/cfg/json',
                     {k: v for k, v in cfg.items() if k != '_raw'})
        for k, v in cfg.items():
            if v is not None and k != '_raw':
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v))

    # ── CFG polling ──────────────────────────────────────────────────────────
    def maybe_poll_cfg(self):
        if TEMZIT_CFG_INTERVAL <= 0:
            return
        now = time.time()
        if now - self.last_cfg_poll < TEMZIT_CFG_INTERVAL:
            return
        wait = self.last_sync_ts + TEMZIT_CFG_DELAY_AFTER_SYNC - now
        if wait > 0:
            time.sleep(wait)
        cfg = self.temzit.get_cfg()
        self._publish_cfg(cfg)
        self._flush_pending_set()
        self.last_cfg_poll = time.time()

    # ── Main loop ────────────────────────────────────────────────────────────
    def loop(self):
        self.client.will_set(f'{MQTT_PREFIX}/availability', 'offline', retain=True)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        self.publish_discovery()
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

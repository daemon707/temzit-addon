#!/usr/bin/env python3
import os, time, json, socket, threading
import paho.mqtt.client as mqtt

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

VERSION = "0.3.0"

CMD_SYNC   = 0x30
CMD_REQCFG = 0x34
CMD_SETCFG = 0x35   # записать конфиг
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

# --- Режимы тепловного насоса (P1, байт mode_code) ---
# mode_code из SYNC-ответа → human-readable
MODE_CODE_TO_HA = {
    0: 'off',
    1: 'heat',
    2: 'heat',   # «Быстрый» → тоже heat (дополнительно передаётся в sensor)
    3: 'heat',   # «Только ТЭН»
    4: 'cool',
}
# Команды HA → P1 value
HA_MODE_TO_P1 = {
    'off':  0,
    'heat': 1,
    'cool': 4,
}
HA_MODES = ['off', 'heat', 'cool']

# P1 raw value → читаемая строка (для sensor)
P1_NAMES = {0:'Стоп', 1:'Нагрев', 2:'Быстрый', 3:'ТЭН', 4:'Холод', 5:'Режим5'}

# --- Утилиты протокола ---
def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF

def u16le(buf: bytes, off: int):
    if len(buf) < off + 2:
        return None
    return int.from_bytes(buf[off:off+2], 'little', signed=False)

FLOWMETER_TYPES = {
    0:'unknown', 1:'impulse_1l', 2:'impulse_10l',
    3:'dual_channel', 4:'electronic', 5:'fixed', 6:'reed_switch'
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

def heater_stage_from_raw(v):
    if v is None: return None
    if 9   <= v <= 84:  return 1
    if 85  <= v <= 169: return 2
    if 170 <= v <= 255: return 3
    return 0

def dhw_heater_on(v):
    if v is None: return None
    return 'ON' if (v & 0x1) else 'OFF'

def decode_alarm(v):
    if v is None: return None
    names = [name for bit, name in ALARM_BITS.items() if v & bit]
    return 'ok' if not names else ','.join(names)

# --- Построение SET-пакета ---
# Пакет SETCFG: [0x35, len, p0..pN, crc_lo, crc_hi]
# Минимально: задаём p0..p9 (10 байт данных) — первые 10 конфиг-байт
# p0=mode(P1), p1=Troom(P2), p2=Twater(P3), p7=Tdhw, p8=boiler_mode, p9=compressor_limit
def build_setcfg(cfg_bytes: list) -> bytes:
    """cfg_bytes — список из минимум 26 байт (позиции p0..p25)"""
    payload = bytes([CMD_SETCFG, len(cfg_bytes)] + cfg_bytes)
    crc = checksum16(payload)
    return payload + crc.to_bytes(2, 'little')

# --- TCP-клиент ---
class TemzitClient:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.lock = threading.Lock()

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
        p = data[2:62]
        def t(off):
            v = u16le(p, off)
            return None if v is None else v / 10.0
        flow_raw      = u16le(p, 18)
        heater_state  = u16le(p, 24)
        dhw_state     = u16le(p, 26)
        alarm         = u16le(p, 30)
        compressor_hz_1 = p[22] if len(p) > 22 else None
        compressor_hz_2 = p[23] if len(p) > 23 else None
        mode_code = u16le(p, 0)
        return {
            'diag_sync_len': len(data), 'diag_sync_ms': dt,
            'mode_code': mode_code,
            'mode_name': P1_NAMES.get(mode_code, f'mode_{mode_code}'),
            'schedule_no': u16le(p, 2),
            't_outdoor':      t(4),  't_room':         t(6),
            't_supply':       t(8),  't_return':       t(10),
            't_freon_gas':    t(12), 't_freon_liquid': t(14),
            't_dhw':          t(16),
            'flow_raw': flow_raw,
            'flow_l_min': None if flow_raw is None else flow_raw * 4,
            'compressor_type':   p[20] if len(p) > 20 else None,
            'compressor_active': p[21] if len(p) > 21 else None,
            'compressor_hz_1': compressor_hz_1,
            'compressor_hz_2': compressor_hz_2,
            'heater_state_raw': heater_state,
            'heater_stage':     heater_stage_from_raw(heater_state),
            'dhw_heater_state_raw': dhw_state,
            'dhw_heater_on':    dhw_heater_on(dhw_state),
            'power_kw': None if u16le(p, 28) is None else u16le(p, 28) / 100.0,
            'alarm': alarm, 'alarm_text': decode_alarm(alarm),
            'active_schedule_no':   p[45] if len(p) > 45 else None,
            'active_schedule_mode': p[46] if len(p) > 46 else None,
            'set_room':             p[49] if len(p) > 49 else None,
            'set_water':            p[50] if len(p) > 50 else None,
            'set_dhw':              p[51] if len(p) > 51 else None,
            'set_compressor_limit': p[52] if len(p) > 52 else None,
            'set_ten_mode':         p[53] if len(p) > 53 else None,
            'set_dhw_mode':         p[54] if len(p) > 54 else None,
            'weekday': p[56] if len(p) > 56 else None,
            'hour':    p[57] if len(p) > 57 else None,
            'minute':  p[58] if len(p) > 58 else None,
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
        p = data[1:31]
        flowmeter_type = p[22] if len(p) > 22 else None
        return {
            'diag_cfg_len': len(data), 'diag_cfg_ms': dt,
            'cfg_mode':                    p[0]  if len(p) > 0  else None,
            'cfg_room_target':             p[1]  if len(p) > 1  else None,
            'cfg_water_target':            p[2]  if len(p) > 2  else None,
            'cfg_ten_on_outdoor':          p[3]  if len(p) > 3  else None,
            'cfg_kkb_off_outdoor':         p[4]  if len(p) > 4  else None,
            'cfg_p5':                      p[5]  if len(p) > 5  else None,
            'cfg_p6':                      p[6]  if len(p) > 6  else None,
            'cfg_dhw_target':              p[7]  if len(p) > 7  else None,
            'cfg_boiler_mode':             p[8]  if len(p) > 8  else None,
            'cfg_compressor_limit':        p[9]  if len(p) > 9  else None,
            'cfg_weather_comp':            p[18] if len(p) > 18 else None,
            'cfg_dhw_max_from_compressor': p[21] if len(p) > 21 else None,
            'cfg_flowmeter_type':          flowmeter_type,
            'cfg_flowmeter_type_name':     FLOWMETER_TYPES.get(flowmeter_type, f'unknown_{flowmeter_type}'),
            'cfg_backup_type':             p[25] if len(p) > 25 else None,
            # Сохраняем сырые байты для использования в SET
            '_raw': list(p),
        }

    def set_cfg(self, cfg_raw: list, updates: dict) -> None:
        """
        cfg_raw  — список байт из get_cfg()['_raw'] (p[0..29])
        updates  — словарь {offset: value} для изменения
        """
        new_cfg = list(cfg_raw)
        for offset, value in updates.items():
            if offset < len(new_cfg):
                new_cfg[offset] = int(value)
        packet = build_setcfg(new_cfg)
        with self.lock:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(packet)
                # Ответ — просто ACK или новый RESP_CONFIG, читаем и игнорируем
                try:
                    s.recv(64)
                except Exception:
                    pass


# --- MQTT Bridge ---
class Bridge:
    def __init__(self):
        self.temzit = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect    = self.on_connect
        self.client.on_message    = self.on_message
        self.discovery_sent = False
        self.last_cfg_poll  = 0
        self.last_sync_ts   = 0
        self._last_cfg_raw  = None   # кешируем для SET-операций
        self._pending_set   = {}     # очередь {offset: value}
        self._set_lock      = threading.Lock()

    # --- MQTT helpers ---
    def publish(self, topic, payload, retain=True, qos=0):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=qos, retain=retain)

    # --- Subscribe ---
    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')
        # Подписываемся на команды climate
        client.subscribe(f'{MQTT_PREFIX}/climate/set_mode')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_temperature')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_water_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_dhw_temp')
        client.subscribe(f'{MQTT_PREFIX}/climate/set_compressor_limit')
        # Произвольная запись байта: payload = {"offset": N, "value": V}
        client.subscribe(f'{MQTT_PREFIX}/cmd/set_byte')

    def on_message(self, client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        try:
            self._handle_cmd(topic, payload)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'cmd_error': str(e), 'topic': topic, 'payload': payload})

    def _handle_cmd(self, topic, payload):
        suffix = topic.split('/')[-1]

        # --- Карта команд: suffix → (cfg_offset, value_transform) ---
        if suffix == 'set_mode':
            p1 = HA_MODE_TO_P1.get(payload.lower())
            if p1 is None:
                raise ValueError(f'Unknown mode: {payload}')
            self._queue_set(0, p1)

        elif suffix == 'set_temperature':
            # Температура комнаты (Тдома), P2, offset=1, диапазон 16..30
            v = round(float(payload))
            v = max(16, min(30, v))
            self._queue_set(1, v)

        elif suffix == 'set_water_temp':
            # Температура теплоносителя (Тводы), P3, offset=2, диапазон 5..55
            v = round(float(payload))
            v = max(5, min(55, v))
            self._queue_set(2, v)

        elif suffix == 'set_dhw_temp':
            # Температура ГВС, P4→offset=7, диапазон 30..65
            v = round(float(payload))
            v = max(30, min(65, v))
            self._queue_set(7, v)

        elif suffix == 'set_compressor_limit':
            # Лимит ККБ, offset=9, диапазон 0..11
            v = round(float(payload))
            v = max(0, min(11, v))
            self._queue_set(9, v)

        elif suffix == 'set_byte':
            data = json.loads(payload)
            self._queue_set(int(data['offset']), int(data['value']))

    def _queue_set(self, offset: int, value: int):
        """Добавляем в очередь и сразу применяем если есть кеш конфига."""
        with self._set_lock:
            self._pending_set[offset] = value
        self._flush_pending_set()

    def _flush_pending_set(self):
        with self._set_lock:
            if not self._pending_set:
                return
            if self._last_cfg_raw is None:
                # Конфиг ещё не получен — подождём следующего цикла
                return
            updates = dict(self._pending_set)
            self._pending_set.clear()
        self.temzit.set_cfg(self._last_cfg_raw, updates)
        # Форсируем обновление состояния через 2 сек
        threading.Timer(2.0, self._force_sync).start()

    def _force_sync(self):
        try:
            state = self.temzit.get_sync()
            self._publish_state(state)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'force_sync_error': str(e)})

    # --- Discovery ---
    def publish_discovery(self):
        if self.discovery_sent:
            return
        device = {
            'identifiers': ['temzit_hp_1'],
            'name': 'Temzit Heat Pump',
            'manufacturer': 'ТЭМЗИТ',
            'model': 'Hydromodule',
            'sw_version': VERSION,
        }
        avail = {
            'availability_topic': f'{MQTT_PREFIX}/availability',
            'payload_available': 'online',
            'payload_not_available': 'offline',
        }

        # ===== CLIMATE entity =====
        climate_cfg = {
            'name': 'Temzit',
            'uniq_id': 'temzit_climate',
            'object_id': 'temzit_climate',
            'device': device,
            **avail,
            # Текущая температура комнаты
            'curr_temp_t':     f'{MQTT_PREFIX}/state/t_room',
            # Уставка (set_room из SYNC или cfg_room_target из CFG)
            'temp_stat_t':     f'{MQTT_PREFIX}/state/climate_target_temp',
            'temp_cmd_t':      f'{MQTT_PREFIX}/climate/set_temperature',
            'temp_step':       1,
            'min_temp':        16,
            'max_temp':        30,
            # Режим
            'mode_stat_t':     f'{MQTT_PREFIX}/state/ha_mode',
            'mode_cmd_t':      f'{MQTT_PREFIX}/climate/set_mode',
            'modes':           HA_MODES,
            # Precision
            'precision':       0.1,
        }
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_climate/config', climate_cfg)

        # ===== CLIMATE entity для ГВС =====
        dhw_cfg = {
            'name': 'Temzit ГВС',
            'uniq_id': 'temzit_dhw_climate',
            'object_id': 'temzit_dhw_climate',
            'device': device,
            **avail,
            'curr_temp_t':  f'{MQTT_PREFIX}/state/t_dhw',
            'temp_stat_t':  f'{MQTT_PREFIX}/state/cfg_dhw_target',
            'temp_cmd_t':   f'{MQTT_PREFIX}/climate/set_dhw_temp',
            'temp_step':    1,
            'min_temp':     30,
            'max_temp':     65,
            'mode_stat_t':  f'{MQTT_PREFIX}/state/dhw_ha_mode',
            'mode_cmd_t':   f'{MQTT_PREFIX}/climate/set_mode',
            'modes':        ['off', 'heat'],
            'precision':    1,
        }
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_dhw_climate/config', dhw_cfg)

        # ===== SENSORS =====
        sensors = [
            ('outdoor_temperature',    'Температура улица',        't_outdoor',       'temperature', '°C'),
            ('room_temperature',       'Температура дом',          't_room',          'temperature', '°C'),
            ('supply_temperature',     'Температура подачи',       't_supply',        'temperature', '°C'),
            ('return_temperature',     'Температура обратки',      't_return',        'temperature', '°C'),
            ('dhw_temperature',        'Температура ГВС',          't_dhw',           'temperature', '°C'),
            ('freon_gas_temperature',  'Фреон газ',                't_freon_gas',     'temperature', '°C'),
            ('freon_liquid_temperature','Фреон жидкость',          't_freon_liquid',  'temperature', '°C'),
            ('power_kw',               'Мощность',                 'power_kw',        'power',       'kW'),
            ('flow_l_min',             'Проток',                   'flow_l_min',      None,          'L/min'),
            ('compressor_hz_1',        'ККБ1 частота',             'compressor_hz_1', 'frequency',   'Hz'),
            ('compressor_hz_2',        'ККБ2 частота',             'compressor_hz_2', 'frequency',   'Hz'),
            ('heater_stage',           'Ступень ТЭНа',             'heater_stage',    None,          None),
            ('mode_name',              'Режим работы',             'mode_name',       None,          None),
            ('set_water',              'Уставка t воды',           'set_water',       'temperature', '°C'),
            ('cfg_water_target',       'Конфиг t воды',            'cfg_water_target','temperature', '°C'),
            ('cfg_dhw_target',         'Конфиг t ГВС',             'cfg_dhw_target',  'temperature', '°C'),
            ('cfg_weather_comp',       'Погодная компенсация',     'cfg_weather_comp',None,          None),
            ('compressor_limit',       'Лимит компрессора',        'set_compressor_limit', None,     '%'),
            ('diag_sync_ms',           'Ping SYNC (мс)',            'diag_sync_ms',    None,          'ms'),
            ('diag_cfg_ms',            'Ping CFG (мс)',             'diag_cfg_ms',     None,          'ms'),
        ]
        for object_id, name, field, devcls, unit in sensors:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{object_id}',
                'object_id': f'temzit_{object_id}',
                'stat_t': f'{MQTT_PREFIX}/state/{field}',
                'device': device,
                **avail,
            }
            if devcls: cfg['dev_cla'] = devcls
            if unit:   cfg['unit_of_meas'] = unit
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{object_id}/config', cfg)

        # ===== BINARY SENSORS =====
        bin_sensors = [
            ('dhw_heater_on',      'ТЭН БКН',              'dhw_heater_on'),
            ('compressor_active',  'Компрессор активен',    'compressor_active'),
        ]
        for object_id, name, field in bin_sensors:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{object_id}',
                'object_id': f'temzit_{object_id}',
                'stat_t': f'{MQTT_PREFIX}/state/{field}',
                'payload_on': 'ON', 'payload_off': 'OFF',
                'device': device,
                **avail,
            }
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/binary_sensor/temzit_{object_id}/config', cfg)

        # compressor_active — числовое значение, конвертируем
        # (публикуется отдельно в _publish_state)

        # ===== ALARM sensor =====
        alarm_cfg = {
            'name': 'Авария',
            'uniq_id': 'temzit_alarm_text',
            'object_id': 'temzit_alarm_text',
            'stat_t': f'{MQTT_PREFIX}/state/alarm_text',
            'device': device,
            **avail,
        }
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_alarm_text/config', alarm_cfg)

        # ===== NUMBER entities (уставки с возможностью записи) =====
        numbers = [
            ('water_temp_target', 'Уставка t воды',  f'{MQTT_PREFIX}/state/cfg_water_target',
             f'{MQTT_PREFIX}/climate/set_water_temp', 5, 55, 1, '°C'),
            ('dhw_temp_target',   'Уставка t ГВС',   f'{MQTT_PREFIX}/state/cfg_dhw_target',
             f'{MQTT_PREFIX}/climate/set_dhw_temp',   30, 65, 1, '°C'),
        ]
        for obj_id, name, stat_t, cmd_t, mn, mx, step, unit in numbers:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{obj_id}',
                'object_id': f'temzit_{obj_id}',
                'stat_t': stat_t,
                'cmd_t': cmd_t,
                'min': mn, 'max': mx, 'step': step,
                'unit_of_meas': unit,
                'mode': 'box',
                'device': device,
                **avail,
            }
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/number/temzit_{obj_id}/config', cfg)

        self.discovery_sent = True

    # --- State publishing ---
    def _publish_state(self, state: dict):
        # ha_mode — переводим mode_code в HA-режим
        mode_code = state.get('mode_code')
        ha_mode = MODE_CODE_TO_HA.get(mode_code, 'off')
        state['ha_mode'] = ha_mode

        # climate_target_temp — для climate entity берём set_room если есть, иначе cfg_room_target
        target = state.get('set_room') or state.get('cfg_room_target')
        state['climate_target_temp'] = target

        # compressor_active → ON/OFF для binary_sensor
        ca = state.get('compressor_active')
        state['compressor_active'] = 'ON' if ca else 'OFF'

        # DHW mode
        dhw_mode = 'heat' if ha_mode != 'off' else 'off'
        state['dhw_ha_mode'] = dhw_mode

        self.publish(f'{MQTT_PREFIX}/state/json', state)
        for k, v in state.items():
            if v is not None and not k.startswith('_'):
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v) if not isinstance(v, (dict, list)) else json.dumps(v))

    def _publish_cfg(self, cfg: dict):
        self._last_cfg_raw = cfg.get('_raw')
        self.publish(f'{MQTT_PREFIX}/cfg/json', {k: v for k, v in cfg.items() if k != '_raw'})
        for k, v in cfg.items():
            if v is not None and k != '_raw':
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v))

    # --- CFG polling ---
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
        # Применяем pending set если был накоплен до получения конфига
        self._flush_pending_set()
        self.last_cfg_poll = time.time()

    # --- Main loop ---
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

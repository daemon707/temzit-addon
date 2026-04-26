#!/usr/bin/env python3
"""
Temzit MQTT Bridge v0.7.1 SAFE
- Чтение cfg: data[2:32]
- Маппинг параметров по подтверждённому реверс-инжинирингу: P1..P9/P88
- Защита от сдвинутого буфера: перед set_cfg запрещается отправка cfg_raw,
  если первый байт не похож на mode (0..5)
- Используется last_good_cfg_raw как эталон записи
- Публикуется диагностический топик temzit/diag/set_guard
"""
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
VERSION = '0.7.1'

CMD_SYNC = 0x30
CMD_REQCFG = 0x34
CMD_SETCFG = 0x35
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

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
AUX_HEATER_NAMES = {64: '0%', 65: '30%', 66: '60%', 67: '100%'}
DHW_MODE_NAMES = {0: 'Выключен', 1: 'Только ТЭН в баке', 2: 'ТН 10%', 3: 'ТН 20%', 4: 'ТН 30%', 5: 'ТН 40%', 6: 'ТН 50%', 7: 'ТН 60%', 8: 'ТН 70%', 9: 'ТН 80%', 10: 'ТН 90%', 11: 'ТН 100%'}
COMP_LIMIT_NAMES = {0: 'Без ограничений', 1: '10%', 2: '20%', 3: '30%', 4: '40%', 5: '50%', 6: '55%', 7: '60%', 8: '70%', 9: '80%', 10: '90%'}
COMP_LIMIT_PCT = {0: 0, 1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 55, 7: 60, 8: 70, 9: 80, 10: 90}
BACKUP_TYPE_NAMES = {0: 'Не использовать', 1: 'после I ступени', 2: 'после II ступени', 3: 'после III ступени', 4: 'только внешний'}
FLOWMETER_TYPES = {0: 'unknown', 1: 'impulse_1l', 2: 'impulse_10l', 3: 'dual_channel', 4: 'electronic', 5: 'fixed', 6: 'reed_switch'}
ALARM_BITS = {0x0001: 'contactor_fail_e05', 0x0002: 'link_fail_e08', 0x0004: 'flow1_fail_e01', 0x0008: 'wifiterm_fail_e07', 0x0010: 'tevap_fail_e09', 0x0020: 'tcompover_fail_e02', 0x0040: 'tevaplow_fail_e03', 0x0080: 'kkb1_fail_e04', 0x0100: 'clock_fail_e06', 0x0200: 'wifi_error_e0A', 0x4000: 'crit_tsens_fail', 0x8000: 'lcdversion_fail'}


def s8(v):
    return None if v is None else (v if v < 128 else v - 256)


def u8(v):
    return int(v) & 0xFF


def weather_comp_from_raw(v):
    return None if v is None else round(v * 0.1, 1)


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def u16le(buf: bytes, off: int):
    if len(buf) < off + 2:
        return None
    return int.from_bytes(buf[off:off + 2], 'little', signed=False)


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


def build_setcfg(cfg_bytes: list) -> bytes:
    if len(cfg_bytes) != 30:
        raise ValueError(f'build_setcfg expects 30 cfg bytes, got {len(cfg_bytes)}')
    payload = bytes([CMD_SETCFG] + [u8(x) for x in cfg_bytes])
    crc = sum(payload) & 0xFF
    return payload + bytes([crc])


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
        if len(data) < 64:
            raise ValueError(f'incomplete sync reply: {len(data)} bytes')
        if data[0] != RESP_ACTUAL:
            raise ValueError(f'unexpected sync reply type: {data[0]}')
        crc_rx = int.from_bytes(data[62:64], 'little')
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'sync CRC mismatch: rx={crc_rx} calc={crc_calc}')
        p = data[2:62]
        def t(off):
            v = u16le(p, off)
            return None if v is None else v / 10.0
        flow_raw = u16le(p, 18)
        heater_state = u16le(p, 24)
        dhw_state = u16le(p, 26)
        alarm = u16le(p, 30)
        mode_code = u16le(p, 0)
        power_raw = u16le(p, 28)
        set_compressor_limit_raw = p[52] if len(p) > 52 else None
        return {
            'diag_sync_len': len(data), 'diag_sync_ms': dt, '_sync_raw': list(p),
            'mode_code': mode_code, 'mode_name': P1_NAMES.get(mode_code, f'mode_{mode_code}'),
            'schedule_no': u16le(p, 2), 't_outdoor': t(4), 't_room': t(6), 't_supply': t(8), 't_return': t(10),
            't_freon_gas': t(12), 't_freon_liquid': t(14), 't_dhw': t(16),
            'flow_raw': flow_raw, 'flow_l_min': None if flow_raw is None else round(flow_raw * 4, 2),
            'compressor_type': p[20] if len(p) > 20 else None, 'compressor_model': p[21] if len(p) > 21 else None,
            'compressor_hz_1': p[22] if len(p) > 22 else None, 'compressor_hz_2': p[23] if len(p) > 23 else None,
            'compressor_active': 'ON' if ((p[22] if len(p) > 22 else 0) or 0) > 0 or ((p[23] if len(p) > 23 else 0) or 0) > 0 else 'OFF',
            'heater_state_raw': heater_state, 'heater_stage': heater_stage_from_raw(heater_state),
            'dhw_heater_state_raw': dhw_state, 'dhw_heater_on': dhw_heater_on(dhw_state),
            'power_kw': None if power_raw is None else round(power_raw / 10.0, 1), 'alarm': alarm, 'alarm_text': decode_alarm(alarm),
            'active_schedule_no': p[45] if len(p) > 45 else None, 'active_schedule_mode': p[46] if len(p) > 46 else None,
            'set_room': p[47] if len(p) > 47 else None, 'set_water': p[49] if len(p) > 49 else None, 'set_dhw': p[51] if len(p) > 51 else None,
            'set_compressor_limit': set_compressor_limit_raw, 'set_compressor_limit_pct': COMP_LIMIT_PCT.get(set_compressor_limit_raw),
            'set_compressor_limit_name': COMP_LIMIT_NAMES.get(set_compressor_limit_raw, str(set_compressor_limit_raw)),
            'set_ten_mode': p[53] if len(p) > 53 else None, 'set_dhw_mode': p[54] if len(p) > 54 else None,
            'set_dhw_mode_name': DHW_MODE_NAMES.get(p[54] if len(p) > 54 else None, '?'),
            'weekday': p[56] if len(p) > 56 else None, 'hour': p[57] if len(p) > 57 else None, 'minute': p[58] if len(p) > 58 else None, 'second': p[59] if len(p) > 59 else None,
        }

    def get_cfg(self) -> dict:
        data, dt = self._query(bytes([CMD_REQCFG, 0x00]))
        if len(data) < 35:
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
        aux_heater_raw = p[CFG_OFFSET_AUX_HEATER_MODE]
        dhw_mode_raw = p[CFG_OFFSET_DHW_MODE]
        comp_limit_raw = p[CFG_OFFSET_COMP_LIMIT]
        weather_raw = p[CFG_OFFSET_WEATHER_COMP] if len(p) > CFG_OFFSET_WEATHER_COMP else None
        backup_raw = p[CFG_OFFSET_BACKUP_TYPE]
        flowmeter_raw = p[CFG_OFFSET_FLOWMETER] if len(p) > CFG_OFFSET_FLOWMETER else None
        return {
            'diag_cfg_len': len(data), 'diag_cfg_ms': dt,
            'cfg_mode': p[CFG_OFFSET_MODE], 'cfg_room_target': p[CFG_OFFSET_ROOM_TARGET], 'cfg_water_target': p[CFG_OFFSET_WATER_TARGET],
            'cfg_aux_heater_mode': aux_heater_raw, 'cfg_aux_heater_mode_name': AUX_HEATER_NAMES.get(aux_heater_raw, str(aux_heater_raw)),
            'cfg_ten_on_outdoor': s8(p[CFG_OFFSET_TEN_ON_OUTDOOR]), 'cfg_kkb_off_outdoor': s8(p[CFG_OFFSET_KKB_OFF_OUTDOOR]),
            'cfg_backup_type': backup_raw, 'cfg_backup_type_name': BACKUP_TYPE_NAMES.get(backup_raw, str(backup_raw)),
            'cfg_dhw_target': p[CFG_OFFSET_DHW_TARGET], 'cfg_dhw_mode': dhw_mode_raw, 'cfg_dhw_mode_name': DHW_MODE_NAMES.get(dhw_mode_raw, str(dhw_mode_raw)),
            'cfg_compressor_limit': comp_limit_raw, 'cfg_compressor_limit_pct': COMP_LIMIT_PCT.get(comp_limit_raw), 'cfg_compressor_limit_name': COMP_LIMIT_NAMES.get(comp_limit_raw, str(comp_limit_raw)),
            'cfg_weather_comp': weather_comp_from_raw(weather_raw), 'cfg_dhw_max_from_compressor': p[CFG_OFFSET_DHW_MAX_COMP] if len(p) > CFG_OFFSET_DHW_MAX_COMP else None,
            'cfg_flowmeter_type': flowmeter_raw, 'cfg_flowmeter_type_name': FLOWMETER_TYPES.get(flowmeter_raw, f'unknown_{flowmeter_raw}'), '_raw': p,
        }

    def set_cfg(self, cfg_raw: list, updates: dict) -> dict:
        new_cfg = list(cfg_raw)
        for offset, value in updates.items():
            if not (0 <= offset < len(new_cfg)):
                raise ValueError(f'offset out of range: {offset}')
            new_cfg[offset] = u8(value)
        packet = build_setcfg(new_cfg)
        print(f'set_cfg packet ({len(packet)} bytes): {list(packet)}', flush=True)
        with self.lock:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(packet)
                try:
                    resp = s.recv(64)
                    print(f'set_cfg response ({len(resp)} bytes): {list(resp)}', flush=True)
                except Exception as ex:
                    resp = b''
                    print(f'set_cfg no response: {ex}', flush=True)
        return {'packet': list(packet), 'response': list(resp), 'cfg_raw': cfg_raw, 'new_cfg': new_cfg, 'updates': updates}


class Bridge:
    def __init__(self):
        self.temzit = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
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
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        try:
            self._handle_cmd(topic, payload)
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'cmd_error': str(e), 'topic': topic, 'payload': payload})

    def _handle_cmd(self, topic, payload):
        suffix = topic.split('/')[-1]
        if suffix == 'set_mode':
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

        self.publish(f'{MQTT_PREFIX}/diag/set_guard', {
            'status': 'using', 'chosen': chosen, 'updates': updates, 'base_cfg': base_cfg,
            'current_ok': current_ok, 'current_reason': current_reason, 'good_ok': good_ok, 'good_reason': good_reason,
        }, retain=False)

        try:
            result = self.temzit.set_cfg(base_cfg, updates)
            self.publish(f'{MQTT_PREFIX}/diag/last_set', result, retain=False)
            print(f'set_cfg OK: {updates}', flush=True)
            threading.Timer(2.0, self._force_sync_and_cfg).start()
        except Exception as e:
            print(f'set_cfg ERROR: {e} updates={updates}', flush=True)
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'set_cfg_error': str(e), 'updates': str(updates)})
            with self._set_lock:
                updates.update(self._pending_set)
                self._pending_set = updates

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
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_climate/config', {'name': 'Temzit', 'uniq_id': 'temzit_climate', 'device': device, **avail, 'curr_temp_t': f'{MQTT_PREFIX}/state/t_room', 'temp_stat_t': f'{MQTT_PREFIX}/state/climate_target_temp', 'temp_cmd_t': f'{MQTT_PREFIX}/climate/set_temperature', 'temp_step': 1, 'min_temp': 16, 'max_temp': 30, 'mode_stat_t': f'{MQTT_PREFIX}/state/ha_mode', 'mode_cmd_t': f'{MQTT_PREFIX}/climate/set_mode', 'modes': HA_MODES, 'precision': 0.1})
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_dhw_climate/config', {'name': 'Temzit ГВС', 'uniq_id': 'temzit_dhw_climate', 'device': device, **avail, 'curr_temp_t': f'{MQTT_PREFIX}/state/t_dhw', 'temp_stat_t': f'{MQTT_PREFIX}/state/cfg_dhw_target', 'temp_cmd_t': f'{MQTT_PREFIX}/climate/set_dhw_temp', 'temp_step': 1, 'min_temp': 20, 'max_temp': 70, 'mode_stat_t': f'{MQTT_PREFIX}/state/dhw_ha_mode', 'mode_cmd_t': f'{MQTT_PREFIX}/climate/set_mode', 'modes': ['off', 'heat'], 'precision': 1})
        self.discovery_sent = True

    def _publish_state(self, state: dict):
        mode_code = state.get('mode_code')
        state['ha_mode'] = MODE_CODE_TO_HA.get(mode_code, 'off')
        state['climate_target_temp'] = state.get('set_room') or state.get('cfg_room_target')
        if state.get('compressor_active') is None:
            state['compressor_active'] = 'OFF'
        state['dhw_ha_mode'] = 'heat' if state['ha_mode'] != 'off' else 'off'
        sync_raw = state.get('_sync_raw')
        if sync_raw is not None:
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
                self.publish(f'{MQTT_PREFIX}/state/{k}', str(v))

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

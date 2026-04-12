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
CMD_SYNC = 0x30
CMD_REQCFG = 0x34
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF

def u16le(buf: bytes, off: int):
    if len(buf) < off + 2:
        return None
    return int.from_bytes(buf[off:off+2], 'little', signed=False)

FLOWMETER_TYPES = {0:'unknown',1:'impulse_1l',2:'impulse_10l',3:'dual_channel',4:'electronic',5:'fixed',6:'reed_switch'}
ALARM_BITS = {
    0x1: 'contactor_fail_e05', 0x2: 'link_fail_e08', 0x4: 'flow1_fail_e01', 0x8: 'wifiterm_fail_e07',
    0x10: 'tevap_fail_e09', 0x20: 'tcompover_fail_e02', 0x40: 'tevaplow_fail_e03', 0x80: 'kkb1_fail_e04',
    0x100: 'clock_fail_e06', 0x200: 'wifi_error_e0A', 0x4000: 'crit_tsens_fail', 0x8000: 'lcdversion_fail'
}

def heater_stage_from_raw(v):
    if v is None: return None
    if 9 <= v <= 84: return 1
    if 85 <= v <= 169: return 2
    if 170 <= v <= 255: return 3
    return 0

def dhw_heater_on(v):
    if v is None: return None
    return 'ON' if (v & 0x1) else 'OFF'

def decode_alarm(v):
    if v is None: return None
    names = [name for bit, name in ALARM_BITS.items() if v & bit]
    return 'ok' if not names else ','.join(names)

class TemzitClient:
    def __init__(self, host, port, timeout):
        self.host = host; self.port = port; self.timeout = timeout; self.lock = threading.Lock()
    def _query(self, payload: bytes):
        with self.lock:
            t0 = time.time()
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(payload)
                data = s.recv(256)
                dt = round((time.time() - t0) * 1000)
                return data, dt
    def get_sync(self):
        data, dt = self._query(bytes([CMD_SYNC, 0x00]))
        if len(data) < 4:
            raise TimeoutError(f'sync short reply: {len(data)} bytes')
        if data[0] != RESP_ACTUAL:
            raise ValueError(f'unexpected sync reply type: {data[0]}')
        if len(data) < 64:
            raise ValueError(f'incomplete sync reply: {len(data)} bytes')
        crc_rx = int.from_bytes(data[62:64], 'little', signed=False)
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'sync checksum mismatch: rx={crc_rx} calc={crc_calc}')
        p = data[2:62]
        def t(off):
            v = u16le(p, off)
            return None if v is None else v / 10.0
        flow_raw = u16le(p, 18)
        heater_state = u16le(p, 24)
        dhw_state = u16le(p, 26)
        alarm = u16le(p, 30)
        compressor_hz_1 = p[22] if len(p) > 22 else None
        compressor_hz_2 = p[23] if len(p) > 23 else None
        return {
            'diag_sync_len': len(data), 'diag_sync_ms': dt,
            'mode_code': u16le(p, 0), 'schedule_no': u16le(p, 2),
            't_outdoor': t(4), 't_room': t(6), 't_supply': t(8), 't_return': t(10),
            't_freon_gas': t(12), 't_freon_liquid': t(14), 't_dhw': t(16),
            'flow_raw': flow_raw, 'flow_l_min': None if flow_raw is None else flow_raw * 4,
            'compressor_type': p[20] if len(p) > 20 else None,
            'compressor_active': p[21] if len(p) > 21 else None,
            'compressor_hz_1': compressor_hz_1, 'compressor_hz_2': compressor_hz_2,
            'compressor_rpm_1': compressor_hz_1, 'compressor_rpm_2': compressor_hz_2,
            'heater_state_raw': heater_state, 'heater_stage': heater_stage_from_raw(heater_state),
            'dhw_heater_state_raw': dhw_state, 'dhw_heater_on': dhw_heater_on(dhw_state),
            'power_kw': None if u16le(p, 28) is None else u16le(p, 28) / 100.0,
            'alarm': alarm, 'alarm_text': decode_alarm(alarm),
            'active_schedule_no': p[45] if len(p) > 45 else None,
            'active_schedule_mode': p[46] if len(p) > 46 else None,
            'set_room': p[49] if len(p) > 49 else None,
            'set_water': p[50] if len(p) > 50 else None,
            'set_dhw': p[51] if len(p) > 51 else None,
            'set_compressor_limit': p[52] if len(p) > 52 else None,
            'set_ten_mode': p[53] if len(p) > 53 else None,
            'set_dhw_mode': p[54] if len(p) > 54 else None,
            'weekday': p[56] if len(p) > 56 else None,
            'hour': p[57] if len(p) > 57 else None,
            'minute': p[58] if len(p) > 58 else None,
        }
    def get_cfg(self):
        data, dt = self._query(bytes([CMD_REQCFG, 0x00]))
        if len(data) < 4:
            raise TimeoutError(f'cfg short reply: {len(data)} bytes')
        if data[0] != RESP_CONFIG:
            raise ValueError(f'unexpected cfg reply type: {data[0]}')
        if len(data) < 64:
            raise ValueError(f'incomplete cfg reply: {len(data)} bytes')
        crc_rx = int.from_bytes(data[62:64], 'little', signed=False)
        crc_calc = checksum16(data[:62])
        if crc_rx != crc_calc:
            raise ValueError(f'cfg checksum mismatch: rx={crc_rx} calc={crc_calc}')
        p = data[1:31]
        flowmeter_type = p[22] if len(p) > 22 else None
        return {
            'diag_cfg_len': len(data), 'diag_cfg_ms': dt,
            'cfg_room_target': p[1] if len(p) > 1 else None,
            'cfg_water_target': p[2] if len(p) > 2 else None,
            'cfg_dhw_target': p[7] if len(p) > 7 else None,
            'cfg_boiler_mode': p[8] if len(p) > 8 else None,
            'cfg_compressor_limit': p[9] if len(p) > 9 else None,
            'cfg_weather_comp': p[18] if len(p) > 18 else None,
            'cfg_dhw_max_from_compressor': p[21] if len(p) > 21 else None,
            'cfg_flowmeter_type': flowmeter_type,
            'cfg_flowmeter_type_name': FLOWMETER_TYPES.get(flowmeter_type, f'unknown_{flowmeter_type}'),
            'cfg_backup_type': p[25] if len(p) > 25 else None,
        }

class Bridge:
    def __init__(self):
        self.temzit = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self.on_connect
        self.discovery_sent = False
        self.last_cfg_poll = 0
        self.last_sync_ts = 0
    def publish(self, topic, payload, retain=True):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=0, retain=retain)
    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')
    def publish_discovery(self):
        if self.discovery_sent:
            return
        device = {'identifiers': ['temzit_hp_1'], 'name': 'Temzit Heat Pump', 'manufacturer': 'ТЭМЗИТ', 'model': 'Hydromodule'}
        sensors = [
            ('outdoor_temperature', 'Температура улица', 't_outdoor', 'temperature', '°C'),
            ('room_temperature', 'Температура дом', 't_room', 'temperature', '°C'),
            ('supply_temperature', 'Температура подачи', 't_supply', 'temperature', '°C'),
            ('return_temperature', 'Температура обратки', 't_return', 'temperature', '°C'),
            ('dhw_temperature', 'Температура ГВС', 't_dhw', 'temperature', '°C'),
            ('freon_gas_temperature', 'Фреон t газа', 't_freon_gas', 'temperature', '°C'),
            ('freon_liquid_temperature', 'Фреон t жидкости', 't_freon_liquid', 'temperature', '°C'),
            ('power_kw', 'Мощность', 'power_kw', 'power', 'kW'),
            ('flow_raw', 'Проток raw', 'flow_raw', None, None),
            ('flow_l_min', 'Скорость протока', 'flow_l_min', None, 'L/min'),
            ('compressor_hz_1', 'ККБ1 Гц', 'compressor_hz_1', None, 'Hz'),
            ('compressor_hz_2', 'ККБ2 Гц', 'compressor_hz_2', None, 'Hz'),
            ('heater_state_raw', 'ТЭН raw', 'heater_state_raw', None, None),
            ('heater_stage', 'Ступень ТЭНа', 'heater_stage', None, None),
            ('dhw_heater_state_raw', 'БКН ТЭН raw', 'dhw_heater_state_raw', None, None),
            ('alarm', 'Авария raw', 'alarm', None, None),
            ('diag_sync_len', 'Diag sync len', 'diag_sync_len', None, None),
            ('diag_sync_ms', 'Diag sync ms', 'diag_sync_ms', None, 'ms'),
            ('set_room', 'Расписание t комнаты', 'set_room', 'temperature', '°C'),
            ('set_water', 'Расписание t воды', 'set_water', 'temperature', '°C'),
            ('set_dhw', 'Расписание t ГВС', 'set_dhw', 'temperature', '°C'),
            ('compressor_limit', 'Мощность ККБ %', 'set_compressor_limit', None, '%'),
            ('active_schedule_no', 'Активное расписание', 'active_schedule_no', None, None),
            ('active_schedule_mode', 'Режим расписания', 'active_schedule_mode', None, None),
            ('flowmeter_type', 'Тип расходомера', 'cfg_flowmeter_type', None, None),
            ('flowmeter_type_name', 'Тип расходомера имя', 'cfg_flowmeter_type_name', None, None),
            ('cfg_room_target', 'Конфиг t комнаты', 'cfg_room_target', 'temperature', '°C'),
            ('cfg_water_target', 'Конфиг t воды', 'cfg_water_target', 'temperature', '°C'),
            ('cfg_dhw_target', 'Конфиг t ГВС', 'cfg_dhw_target', 'temperature', '°C'),
            ('cfg_weather_comp', 'Погодозависимая автоматика', 'cfg_weather_comp', None, None),
            ('cfg_backup_type', 'Тип резерва', 'cfg_backup_type', None, None),
            ('diag_cfg_len', 'Diag cfg len', 'diag_cfg_len', None, None),
            ('diag_cfg_ms', 'Diag cfg ms', 'diag_cfg_ms', None, 'ms'),
        ]
        for object_id, name, field, devcls, unit in sensors:
            cfg = {'name': name, 'uniq_id': f'temzit_{object_id}', 'stat_t': f'{MQTT_PREFIX}/state/{field}', 'availability_topic': f'{MQTT_PREFIX}/availability', 'payload_available': 'online', 'payload_not_available': 'offline', 'device': device}
            if devcls: cfg['dev_cla'] = devcls
            if unit: cfg['unit_of_meas'] = unit
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{object_id}/config', cfg)
        bin_sensors = [('dhw_heater_on', 'ТЭН БКН', 'dhw_heater_on')]
        for object_id, name, field in bin_sensors:
            cfg = {'name': name, 'uniq_id': f'temzit_{object_id}', 'stat_t': f'{MQTT_PREFIX}/state/{field}', 'payload_on': 'ON', 'payload_off': 'OFF', 'availability_topic': f'{MQTT_PREFIX}/availability', 'payload_available': 'online', 'payload_not_available': 'offline', 'device': device}
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/binary_sensor/temzit_{object_id}/config', cfg)
        text_sensors = [('alarm_text', 'Авария', 'alarm_text')]
        for object_id, name, field in text_sensors:
            cfg = {'name': name, 'uniq_id': f'temzit_{object_id}', 'stat_t': f'{MQTT_PREFIX}/state/{field}', 'availability_topic': f'{MQTT_PREFIX}/availability', 'payload_available': 'online', 'payload_not_available': 'offline', 'device': device}
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{object_id}/config', cfg)
        self.discovery_sent = True
    def maybe_poll_cfg(self):
        now = time.time()
        if now - self.last_cfg_poll < TEMZIT_CFG_INTERVAL:
            return
        wait_needed = self.last_sync_ts + TEMZIT_CFG_DELAY_AFTER_SYNC - now
        if wait_needed > 0:
            time.sleep(wait_needed)
        cfg = self.temzit.get_cfg()
        self.publish(f'{MQTT_PREFIX}/cfg/json', cfg)
        for k, v in cfg.items():
            if v is not None:
                self.publish(f'{MQTT_PREFIX}/state/{k}', v)
        self.last_cfg_poll = time.time()
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
                self.publish(f'{MQTT_PREFIX}/state/json', state)
                for k, v in state.items():
                    if v is not None:
                        self.publish(f'{MQTT_PREFIX}/state/{k}', v)
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

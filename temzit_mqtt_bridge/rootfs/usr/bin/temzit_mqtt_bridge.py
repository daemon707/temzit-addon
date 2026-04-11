#!/usr/bin/env python3
import os
import time
import json
import socket
import threading
from typing import Dict, List, Tuple

import paho.mqtt.client as mqtt

TEMZIT_HOST = os.getenv('TEMZIT_HOST', '192.168.1.50')
TEMZIT_PORT = int(os.getenv('TEMZIT_PORT', '333'))
TEMZIT_TIMEOUT = float(os.getenv('TEMZIT_TIMEOUT', '5'))
TEMZIT_SYNC_INTERVAL = int(os.getenv('TEMZIT_SYNC_INTERVAL', '30'))
TEMZIT_CFG_INTERVAL = int(os.getenv('TEMZIT_CFG_INTERVAL', '300'))
TEMZIT_RETRY_DELAY = float(os.getenv('TEMZIT_RETRY_DELAY', '3'))

MQTT_HOST = os.getenv('MQTT_HOST', '127.0.0.1')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER = os.getenv('MQTT_USER', '')
MQTT_PASS = os.getenv('MQTT_PASS', '')
MQTT_PREFIX = os.getenv('MQTT_PREFIX', 'temzit')
MQTT_DISCOVERY_PREFIX = os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant')
MQTT_CLIENT_ID = os.getenv('MQTT_CLIENT_ID', 'temzit-bridge')

CMD_SYNC = 0x30
CMD_REQCFG = 0x34
CMD_CFG = 0x35
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02

MODE_MAP = {
    0: 'off',
    1: 'heat',
    2: 'boost',
    3: 'ten_only',
    4: 'cool',
    5: 'forced_dhw',
}
MODE_REVERSE = {v: k for k, v in MODE_MAP.items()}

TEN_MODE_MAP = {
    0: 'off',
    1: 'stage_1',
    2: 'stage_2',
    3: 'stage_3',
}
TEN_MODE_REVERSE = {v: k for k, v in TEN_MODE_MAP.items()}

DHW_MODE_MAP = {
    0: 'off',
    1: 'ten_only',
    2: 'hp_10',
    3: 'hp_20',
    4: 'hp_30',
    5: 'hp_40',
    6: 'hp_50',
    7: 'hp_60',
    8: 'hp_70',
    9: 'hp_80',
    10: 'hp_90',
    11: 'hp_100',
}
DHW_MODE_REVERSE = {v: k for k, v in DHW_MODE_MAP.items()}

CFG_OFFSETS = {
    'mode': 0,
    't_room_target': 1,
    't_water_target': 2,
    'byte3': 3,
    'ten_enable_temp': 4,
    'compressor_cutoff_temp': 5,
    'byte6': 6,
    'dhw_target': 7,
    'boiler_mode': 8,
    'compressor_limit': 9,
    'meter_pulses': 17,
    'weather_comp': 18,
    'collector_byte': 19,
    'pump_relay_mode': 20,
    'dhw_max_from_hp': 21,
    'flowmeter_type': 22,
    'solar_byte': 23,
    'solar_overheat_temp': 24,
    'backup_type': 25,
}


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def checksum8(data: bytes) -> int:
    return sum(data) & 0xFF


def recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError('Connection closed')
        data += chunk
    return data


class TemzitClient:
    def __init__(self, host: str, port: int, timeout: float):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.lock = threading.Lock()

    def request(self, packet: bytes, expected_len: int = 64) -> bytes:
        with self.lock:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(packet)
                return recv_exact(sock, expected_len)

    @staticmethod
    def _u16le(buf: bytes, off: int) -> int:
        return int.from_bytes(buf[off:off + 2], 'little', signed=False)

    def get_sync(self) -> Dict:
        resp = self.request(bytes([CMD_SYNC, 0x00]))
        if len(resp) != 64 or resp[0] != RESP_ACTUAL:
            raise ValueError('Invalid ACTUAL_STATE response')
        crc_rx = int.from_bytes(resp[62:64], 'little', signed=False)
        crc_calc = checksum16(resp[:62])
        if crc_rx != crc_calc:
            raise ValueError('ACTUAL_STATE checksum mismatch')
        p = resp[2:62]
        t = lambda off: self._u16le(p, off) / 10.0
        mode_code = self._u16le(p, 0)
        result = {
            'mode_code': mode_code,
            'mode_name': MODE_MAP.get(mode_code, f'unknown_{mode_code}'),
            'schedule_no': self._u16le(p, 2),
            't_outdoor': t(4),
            't_room': t(6),
            't_supply': t(8),
            't_return': t(10),
            't_freon_gas': t(12),
            't_freon_liquid': t(14),
            't_dhw': t(16),
            'flow': self._u16le(p, 18),
            'compressor_type': p[20],
            'compressor_active': p[21],
            'compressor_rpm_1': p[22],
            'compressor_rpm_2': p[23],
            'heater_state': self._u16le(p, 24),
            'dhw_heater_state': self._u16le(p, 26),
            'power_kw': self._u16le(p, 28) / 100.0,
            'alarm': self._u16le(p, 30),
            'relay_flags_32': p[32],
            'relay_flags_33': p[33],
            'relay_flags_34': p[34],
            'relay_flags_35': p[35],
            't_setpoint_current': p[36],
            'return_limit_current': p[37],
            'step_start_temp_current': p[38],
            'electricity_pulses': self._u16le(p, 39),
            'fw_major': p[43],
            'fw_minor': p[44],
            'active_schedule_no': p[45],
            'active_schedule_mode': p[46],
            'active_schedule_dhw': p[47],
            'active_schedule_ten': p[48],
            'set_room': p[49],
            'set_water': p[50],
            'set_dhw': p[51],
            'set_compressor_limit': p[52],
            'set_ten_mode': p[53],
            'set_dhw_mode': p[54],
            'energy_tariff': p[55],
            'weekday': p[56],
            'hour': p[57],
            'minute': p[58],
            'second': p[59],
        }
        result['hvac_mode'] = 'off' if result['mode_name'] == 'off' else ('cool' if result['mode_name'] == 'cool' else 'heat')
        result['preset_mode'] = result['mode_name'] if result['mode_name'] in ('boost', 'ten_only', 'forced_dhw') else 'normal'
        result['ten_mode_name'] = TEN_MODE_MAP.get(result['set_ten_mode'], f'unknown_{result["set_ten_mode"]}')
        result['dhw_mode_name'] = DHW_MODE_MAP.get(result['set_dhw_mode'], f'unknown_{result["set_dhw_mode"]}')
        return result

    def get_cfg(self) -> Dict:
        resp = self.request(bytes([CMD_REQCFG, 0x00]))
        if len(resp) != 64 or resp[0] != RESP_CONFIG:
            raise ValueError('Invalid CONFIG response')
        crc_rx = int.from_bytes(resp[62:64], 'little', signed=False)
        crc_calc = checksum16(resp[:62])
        if crc_rx != crc_calc:
            raise ValueError('CONFIG checksum mismatch')
        raw = list(resp[1:31])
        byte3 = raw[3]
        byte6 = raw[6]
        cfg = {
            'raw': raw,
            'mode_code': raw[0],
            'mode_name': MODE_MAP.get(raw[0], f'unknown_{raw[0]}'),
            't_room_target': raw[1],
            't_water_target': raw[2],
            'home_inertia': byte3 & 0x0F,
            'ten_mode_code': (byte3 >> 4) & 0x0F,
            'ten_mode_name': TEN_MODE_MAP.get((byte3 >> 4) & 0x0F, f'unknown_{(byte3 >> 4) & 0x0F}'),
            'ten_enable_temp': raw[4],
            'compressor_cutoff_temp': raw[5],
            'disinfection_code': byte6 & 0x0F,
            'dhw_mode_code': (byte6 >> 4) & 0x0F,
            'dhw_mode_name': DHW_MODE_MAP.get((byte6 >> 4) & 0x0F, f'unknown_{(byte6 >> 4) & 0x0F}'),
            'dhw_target': raw[7],
            'boiler_mode': raw[8],
            'compressor_limit': raw[9],
            'meter_pulses': raw[17],
            'weather_comp': raw[18],
            'collector_byte': raw[19],
            'pump_relay_mode': raw[20],
            'dhw_max_from_hp': raw[21],
            'flowmeter_type': raw[22],
            'solar_byte': raw[23],
            'solar_overheat_temp': raw[24],
            'backup_type': raw[25],
        }
        return cfg

    def send_cfg_raw(self, cfg_raw: List[int]) -> Dict:
        if len(cfg_raw) != 30:
            raise ValueError('Need exactly 30 config bytes')
        body = bytes([CMD_CFG]) + bytes(cfg_raw)
        packet = body + bytes([checksum8(body)])
        self.request(packet)
        return self.get_sync()

    def patch_cfg(self, changes: Dict[str, int]) -> Tuple[Dict, Dict]:
        cfg = self.get_cfg()
        raw = cfg['raw'][:]

        for key, value in changes.items():
            if key == 'mode':
                raw[0] = value & 0xFF
            elif key == 't_room_target':
                raw[1] = value & 0xFF
            elif key == 't_water_target':
                raw[2] = value & 0xFF
            elif key == 'home_inertia':
                raw[3] = (raw[3] & 0xF0) | (value & 0x0F)
            elif key == 'ten_mode':
                raw[3] = (raw[3] & 0x0F) | ((value & 0x0F) << 4)
            elif key == 'ten_enable_temp':
                raw[4] = value & 0xFF
            elif key == 'compressor_cutoff_temp':
                raw[5] = value & 0xFF
            elif key == 'disinfection':
                raw[6] = (raw[6] & 0xF0) | (value & 0x0F)
            elif key == 'dhw_mode':
                raw[6] = (raw[6] & 0x0F) | ((value & 0x0F) << 4)
            elif key == 'dhw_target':
                raw[7] = value & 0xFF
            elif key == 'boiler_mode':
                raw[8] = value & 0xFF
            elif key == 'compressor_limit':
                raw[9] = value & 0xFF
            elif key == 'weather_comp':
                raw[18] = value & 0xFF
            elif key == 'pump_relay_mode':
                raw[20] = value & 0xFF
            elif key == 'dhw_max_from_hp':
                raw[21] = value & 0xFF
            else:
                raise ValueError(f'Unsupported setting: {key}')

        self.send_cfg_raw(raw)
        return self.get_cfg(), self.get_sync()


class Bridge:
    def __init__(self):
        self.temzit = TemzitClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.stop_event = threading.Event()
        self.last_cfg = {}
        self.last_state = {}

    def publish(self, topic: str, payload, retain: bool = True):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=0, retain=retain)

    def publish_state(self, state: Dict, cfg: Dict = None):
        self.last_state = state
        if cfg is not None:
            self.last_cfg = cfg
        self.publish(f'{MQTT_PREFIX}/state/json', state)
        for k, v in state.items():
            self.publish(f'{MQTT_PREFIX}/state/{k}', v)
        if self.last_cfg:
            self.publish(f'{MQTT_PREFIX}/cfg/json', self.last_cfg)
            for k, v in self.last_cfg.items():
                if k != 'raw':
                    self.publish(f'{MQTT_PREFIX}/cfg/{k}', v)
        self.publish(f'{MQTT_PREFIX}/availability', 'online')

    def publish_discovery(self):
        device = {
            'identifiers': ['temzit_hp_1'],
            'name': 'Temzit Heat Pump',
            'manufacturer': 'ТЭМЗИТ',
            'model': 'Hydromodule',
            'sw_version': self.last_state.get('fw_major', '') and f"{self.last_state.get('fw_major')}.{self.last_state.get('fw_minor')}"
        }

        climate_cfg = {
            'name': 'Temzit Heating',
            'uniq_id': 'temzit_heating',
            'mode_cmd_t': f'{MQTT_PREFIX}/set/hvac_mode',
            'mode_stat_t': f'{MQTT_PREFIX}/state/hvac_mode',
            'modes': ['off', 'heat', 'cool'],
            'temp_cmd_t': f'{MQTT_PREFIX}/set/target_temperature',
            'temp_stat_t': f'{MQTT_PREFIX}/state/set_room',
            'curr_temp_t': f'{MQTT_PREFIX}/state/t_room',
            'min_temp': 10,
            'max_temp': 35,
            'temp_step': 1,
            'preset_mode_cmd_t': f'{MQTT_PREFIX}/set/preset_mode',
            'preset_mode_stat_t': f'{MQTT_PREFIX}/state/preset_mode',
            'preset_modes': ['normal', 'boost', 'ten_only', 'forced_dhw'],
            'availability_topic': f'{MQTT_PREFIX}/availability',
            'payload_available': 'online',
            'payload_not_available': 'offline',
            'device': device,
        }
        self.publish(f'{MQTT_DISCOVERY_PREFIX}/climate/temzit_heating/config', climate_cfg)

        sensors = [
            ('outdoor_temperature', 'Темзит улица', 't_outdoor', 'temperature', '°C'),
            ('room_temperature', 'Темзит комната', 't_room', 'temperature', '°C'),
            ('supply_temperature', 'Темзит подача', 't_supply', 'temperature', '°C'),
            ('return_temperature', 'Темзит обратка', 't_return', 'temperature', '°C'),
            ('dhw_temperature', 'Темзит ГВС', 't_dhw', 'temperature', '°C'),
            ('power_kw', 'Темзит мощность', 'power_kw', 'power', 'kW'),
            ('flow', 'Темзит проток', 'flow', None, None),
            ('alarm', 'Темзит авария', 'alarm', None, None),
        ]
        for object_id, name, field, devcls, unit in sensors:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{object_id}',
                'stat_t': f'{MQTT_PREFIX}/state/{field}',
                'availability_topic': f'{MQTT_PREFIX}/availability',
                'payload_available': 'online',
                'payload_not_available': 'offline',
                'device': device,
            }
            if devcls:
                cfg['dev_cla'] = devcls
            if unit:
                cfg['unit_of_meas'] = unit
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/sensor/temzit_{object_id}/config', cfg)

        numbers = [
            ('water_target', 'Темзит вода уставка', 't_water_target', 5, 55),
            ('dhw_target', 'Темзит ГВС уставка', 'dhw_target', 30, 65),
            ('compressor_limit', 'Темзит лимит ККБ', 'compressor_limit', 0, 100),
            ('weather_comp', 'Темзит погодокомпенсация', 'weather_comp', 0, 20),
            ('dhw_max_from_hp', 'Темзит макс ГВС от ТН', 'dhw_max_from_hp', 30, 65),
        ]
        for object_id, name, field, minv, maxv in numbers:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{object_id}',
                'cmd_t': f'{MQTT_PREFIX}/set/{field}',
                'stat_t': f'{MQTT_PREFIX}/cfg/{field}',
                'min': minv,
                'max': maxv,
                'step': 1,
                'mode': 'box',
                'availability_topic': f'{MQTT_PREFIX}/availability',
                'payload_available': 'online',
                'payload_not_available': 'offline',
                'device': device,
            }
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/number/temzit_{object_id}/config', cfg)

        selects = [
            ('mode_name', 'Темзит режим', 'mode_name', list(MODE_REVERSE.keys())),
            ('ten_mode_name', 'Темзит ТЭН режим', 'ten_mode_name', list(TEN_MODE_REVERSE.keys())),
            ('dhw_mode_name', 'Темзит ГВС режим', 'dhw_mode_name', list(DHW_MODE_REVERSE.keys())),
        ]
        for object_id, name, field, options in selects:
            cfg = {
                'name': name,
                'uniq_id': f'temzit_{object_id}',
                'cmd_t': f'{MQTT_PREFIX}/set/{field}',
                'stat_t': f'{MQTT_PREFIX}/cfg/{field}',
                'options': options,
                'availability_topic': f'{MQTT_PREFIX}/availability',
                'payload_available': 'online',
                'payload_not_available': 'offline',
                'device': device,
            }
            self.publish(f'{MQTT_DISCOVERY_PREFIX}/select/temzit_{object_id}/config', cfg)

    def refresh(self, full_cfg: bool = False):
        state = self.temzit.get_sync()
        cfg = self.temzit.get_cfg() if full_cfg or not self.last_cfg else self.last_cfg
        self.publish_state(state, cfg)
        self.publish_discovery()

    def handle_set(self, topic: str, payload: str):
        leaf = topic.split('/')[-1]
        changes = {}

        if leaf == 'hvac_mode':
            val = payload.strip()
            changes['mode'] = {'off': 0, 'heat': 1, 'cool': 4}[val]
        elif leaf == 'preset_mode':
            val = payload.strip()
            mapping = {'normal': 1, 'boost': 2, 'ten_only': 3, 'forced_dhw': 5}
            changes['mode'] = mapping[val]
        elif leaf == 'target_temperature':
            changes['t_room_target'] = int(float(payload))
        elif leaf == 't_water_target':
            changes['t_water_target'] = int(float(payload))
        elif leaf == 'dhw_target':
            changes['dhw_target'] = int(float(payload))
        elif leaf == 'compressor_limit':
            changes['compressor_limit'] = int(float(payload))
        elif leaf == 'weather_comp':
            changes['weather_comp'] = int(float(payload))
        elif leaf == 'dhw_max_from_hp':
            changes['dhw_max_from_hp'] = int(float(payload))
        elif leaf == 'mode_name':
            changes['mode'] = MODE_REVERSE[payload.strip()]
        elif leaf == 'ten_mode_name':
            changes['ten_mode'] = TEN_MODE_REVERSE[payload.strip()]
        elif leaf == 'dhw_mode_name':
            changes['dhw_mode'] = DHW_MODE_REVERSE[payload.strip()]
        else:
            raise ValueError(f'Unknown set topic: {topic}')

        cfg, state = self.temzit.patch_cfg(changes)
        self.publish_state(state, cfg)

    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')
        client.subscribe(f'{MQTT_PREFIX}/set/+')

    def on_message(self, client, userdata, msg):
        try:
            self.handle_set(msg.topic, msg.payload.decode('utf-8'))
        except Exception as e:
            self.publish(f'{MQTT_PREFIX}/availability', 'degraded')
            self.publish(f'{MQTT_PREFIX}/bridge/error', {'topic': msg.topic, 'error': str(e)})

    def loop(self):
        self.client.will_set(f'{MQTT_PREFIX}/availability', 'offline', retain=True)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()

        last_sync = 0
        last_cfg = 0
        while not self.stop_event.is_set():
            now = time.time()
            try:
                if now - last_cfg >= TEMZIT_CFG_INTERVAL or not self.last_cfg:
                    self.refresh(full_cfg=True)
                    last_cfg = now
                    last_sync = now
                elif now - last_sync >= TEMZIT_SYNC_INTERVAL:
                    self.refresh(full_cfg=False)
                    last_sync = now
            except Exception as e:
                self.publish(f'{MQTT_PREFIX}/availability', 'degraded')
                self.publish(f'{MQTT_PREFIX}/bridge/error', {'error': str(e)})
                time.sleep(TEMZIT_RETRY_DELAY)
                continue
            time.sleep(1)


def main():
    Bridge().loop()


if __name__ == '__main__':
    main()

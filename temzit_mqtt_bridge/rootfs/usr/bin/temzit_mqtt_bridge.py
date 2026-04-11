#!/usr/bin/env python3
import os
import time
import json
import socket
import threading
import paho.mqtt.client as mqtt

TEMZIT_HOST = os.getenv('TEMZIT_HOST', '192.168.2.20')
TEMZIT_PORT = int(os.getenv('TEMZIT_PORT', '333'))
TEMZIT_TIMEOUT = int(os.getenv('TEMZIT_TIMEOUT', '15'))
TEMZIT_SYNC_INTERVAL = int(os.getenv('TEMZIT_SYNC_INTERVAL', '60'))
TEMZIT_RETRY_DELAY = int(os.getenv('TEMZIT_RETRY_DELAY', '15'))

MQTT_HOST = os.getenv('MQTT_HOST', '192.168.1.50')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USER = os.getenv('MQTT_USER', '')
MQTT_PASS = os.getenv('MQTT_PASS', '')
MQTT_PREFIX = os.getenv('MQTT_PREFIX', 'temzit')
MQTT_DISCOVERY_PREFIX = os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant')
MQTT_CLIENT_ID = os.getenv('MQTT_CLIENT_ID', 'temzit-bridge')

CMD_SYNC = 0x30
RESP_ACTUAL = 0x01


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def recv_once(sock: socket.socket, n: int) -> bytes:
    return sock.recv(n)


def u16le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off+2], 'little', signed=False)


class TemzitSyncClient:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.lock = threading.Lock()

    def get_sync(self):
        with self.lock:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(bytes([CMD_SYNC, 0x00]))
                data = recv_once(s, 64)
                if len(data) < 20:
                    raise TimeoutError(f'short reply: {len(data)} bytes')
                if data[0] != RESP_ACTUAL:
                    raise ValueError(f'unexpected reply type: {data[0]}')
                if len(data) >= 64:
                    crc_rx = int.from_bytes(data[62:64], 'little', signed=False)
                    crc_calc = checksum16(data[:62])
                    if crc_rx != crc_calc:
                        raise ValueError(f'checksum mismatch: rx={crc_rx} calc={crc_calc}')
                p = data[2:62] if len(data) >= 62 else data[2:]
                t = lambda off: u16le(p, off) / 10.0 if len(p) >= off + 2 else None
                state = {
                    'raw_len': len(data),
                    't_outdoor': t(4),
                    't_room': t(6),
                    't_supply': t(8),
                    't_return': t(10),
                    't_dhw': t(16),
                    'flow': u16le(p, 18) if len(p) >= 20 else None,
                    'alarm': u16le(p, 30) if len(p) >= 32 else None,
                    'hour': p[57] if len(p) >= 58 else None,
                    'minute': p[58] if len(p) >= 59 else None,
                }
                return state


class Bridge:
    def __init__(self):
        self.temzit = TemzitSyncClient(TEMZIT_HOST, TEMZIT_PORT, TEMZIT_TIMEOUT)
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self.on_connect
        self.discovery_sent = False

    def publish(self, topic, payload, retain=True):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, payload, qos=0, retain=retain)

    def on_connect(self, client, userdata, flags, rc):
        self.publish(f'{MQTT_PREFIX}/availability', 'online')

    def publish_discovery(self):
        if self.discovery_sent:
            return
        device = {
            'identifiers': ['temzit_hp_1'],
            'name': 'Temzit Heat Pump',
            'manufacturer': 'ТЭМЗИТ',
            'model': 'Hydromodule',
        }
        sensors = [
            ('outdoor_temperature', 'Темзит улица', 't_outdoor', 'temperature', '°C'),
            ('room_temperature', 'Темзит комната', 't_room', 'temperature', '°C'),
            ('supply_temperature', 'Темзит подача', 't_supply', 'temperature', '°C'),
            ('return_temperature', 'Темзит обратка', 't_return', 'temperature', '°C'),
            ('dhw_temperature', 'Темзит ГВС', 't_dhw', 'temperature', '°C'),
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
        self.discovery_sent = True

    def loop(self):
        self.client.will_set(f'{MQTT_PREFIX}/availability', 'offline', retain=True)
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        self.publish_discovery()
        while True:
            try:
                state = self.temzit.get_sync()
                self.publish(f'{MQTT_PREFIX}/availability', 'online')
                self.publish(f'{MQTT_PREFIX}/state/json', state)
                for k, v in state.items():
                    if v is not None:
                        self.publish(f'{MQTT_PREFIX}/state/{k}', v)
            except Exception as e:
                self.publish(f'{MQTT_PREFIX}/availability', 'degraded')
                self.publish(f'{MQTT_PREFIX}/bridge/error', {'error': str(e)})
            time.sleep(TEMZIT_SYNC_INTERVAL if TEMZIT_SYNC_INTERVAL > 0 else TEMZIT_RETRY_DELAY)


if __name__ == '__main__':
    Bridge().loop()

#!/usr/bin/env python3
"""
Temzit restore — восстановление конфигурации гидромодуля ТЭМЗИТ из файла бэкапа.
Только стандартная библиотека Python 3. Кросс-платформенно (Windows/Linux/macOS).

ЗАПУСК:
    python temzit_restore.py "C:\\Users\\kmdmi\\Downloads\\cfg_20260614_145806_710190.json"
    python temzit_restore.py backup.json --host 192.168.2.20 --port 333
    python temzit_restore.py --hex 01101b42...ddaff --host 192.168.2.20   (без файла, прямой hex)

Что делает:
  1) берёт 30 байт конфигурации из бэкапа (поле cfg_raw или cfg_hex);
  2) проверяет их на адекватность (как guard в мосте) — мусор писать не будет;
  3) шлёт КОРРЕКТНЫЙ кадр записи: 0x35 + 0x00(паддинг) + 30 байт + КС(1 байт) = 33 байта;
  4) ждёт 12с и читает конфиг обратно (0x34), сверяет — СОВПАЛО/НЕ совпало.

ВАЖНО: останови аддон Temzit MQTT Bridge перед запуском, чтобы не делить порт 333.
Запись (0x35) меняет настройки контроллера — запускай осознанно.
"""
import sys, json, socket, time, argparse

EXPECTED_LEN = 64


def load_cfg(args):
    if args.hex:
        raw = list(bytes.fromhex(args.hex.strip().replace(' ', '')))
    else:
        with open(args.backup, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data.get('cfg_raw'), list):
            raw = [int(x) & 0xFF for x in data['cfg_raw']]
        elif data.get('cfg_hex'):
            raw = list(bytes.fromhex(data['cfg_hex']))
        else:
            raise SystemExit('В файле нет ни cfg_raw, ни cfg_hex')
    if len(raw) != 30:
        raise SystemExit(f'Конфиг должен быть ровно 30 байт, получено {len(raw)}')
    return raw


def validate(raw):
    """Та же проверка адекватности, что и в мосте (looks_like_valid_cfg) — чтобы не записать мусор."""
    mode, room, water, aux, dhw_mode, comp = raw[0], raw[1], raw[2], raw[3], raw[8], raw[9]
    if mode not in (0, 1, 2, 3, 4, 5):
        return f'mode={mode}'
    if not (5 <= room <= 45):
        return f'room={room}'
    if not (5 <= water <= 70):
        return f'water={water}'
    if aux not in (64, 65, 66, 67):
        return f'aux={aux}'
    if not (0 <= dhw_mode <= 11):
        return f'dhw_mode={dhw_mode}'
    if not (0 <= comp <= 10):
        return f'comp_limit={comp}'
    return None


def query(host, port, timeout, frame, expect_type, what, retries=5, retry_delay=15, half_close=False):
    last = None
    for attempt in range(1, retries + 1):
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout)
                s.sendall(frame)
                if half_close:
                    # Полузакрытие записи (FIN), как делает `printf | nc` — иначе питон-сокет
                    # давал сдвиг кадра записи на 2 байта (см. кандидатный фикс в мосте).
                    try:
                        s.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                resp = bytearray()
                while len(resp) < EXPECTED_LEN:
                    chunk = s.recv(EXPECTED_LEN - len(resp))
                    if not chunk:
                        break
                    resp.extend(chunk)
            resp = bytes(resp)
            if resp and resp[0] == expect_type:
                return resp
            last = f'тип 0x{(resp[0] if resp else -1) & 0xFF:02x} (ждали 0x{expect_type:02x}), {len(resp)} байт'
        except Exception as e:
            last = str(e)
        print(f'  {what}: попытка {attempt}/{retries} неуспешна: {last}; жду {retry_delay}с (порт ГМ мог быть занят)')
        time.sleep(retry_delay)
    raise SystemExit(f'НЕ УДАЛОСЬ ({what}): {last}')


def main():
    ap = argparse.ArgumentParser(description='Восстановление конфигурации ТЭМЗИТ из бэкапа (read-modify-write безопасным кадром)')
    ap.add_argument('backup', nargs='?', help='путь к файлу бэкапа cfg_*.json')
    ap.add_argument('--hex', help='30 байт конфигурации напрямую (60 hex-символов), вместо файла')
    ap.add_argument('--host', default='192.168.2.20', help='IP гидромодуля (по умолчанию 192.168.2.20)')
    ap.add_argument('--port', type=int, default=333)
    ap.add_argument('--timeout', type=int, default=15)
    ap.add_argument('--yes', action='store_true', help='не спрашивать подтверждение')
    args = ap.parse_args()

    if not args.backup and not args.hex:
        raise SystemExit('Укажи файл бэкапа или --hex. Пример:\n  python temzit_restore.py backup.json --host 192.168.2.20')

    raw = load_cfg(args)
    bad = validate(raw)
    if bad:
        raise SystemExit(f'ОТКАЗ: конфиг не прошёл проверку адекватности ({bad}). Запись отменена.')

    # Кадр записи (подтверждён на железе): frame = [0x35, f1, config[0..29]] = 32 байта.
    # Настройки читаются из frame[2:32]; КС в frame[31]=config[29] = sum(frame[0:31]) & 0xFF;
    # f1 подбирается так, чтобы КС сошлась: f1 = (config[29] - 0x35 - sum(config[0:29])) & 0xFF.
    cfg = [b & 0xFF for b in raw]
    f1 = (cfg[29] - 0x35 - sum(cfg[0:29])) & 0xFF
    frame = bytes([0x35, f1] + cfg)

    print(f'Хост:   {args.host}:{args.port}')
    print(f'Конфиг: {bytes(raw).hex()}')
    print(f'Кадр:   ({len(frame)} б) {frame.hex()}')
    if not args.yes:
        ans = input('Записать этот конфиг на Темзит? Останови аддон заранее. [y/N] ').strip().lower()
        if ans not in ('y', 'yes', 'д', 'да'):
            raise SystemExit('Отменено пользователем.')

    print('Пишу конфиг (0x35)...')
    query(args.host, args.port, args.timeout, frame, 0x01, 'запись', half_close=True)
    print('Запись принята (ACTUAL_STATE). Жду 12с и читаю обратно (0x34)...')
    time.sleep(12)

    resp = query(args.host, args.port, args.timeout, bytes([0x34, 0x00]), 0x02, 'проверка')
    got = list(resp[2:32])
    print(f'Прочитано: {bytes(got).hex()}')
    print(f'Ожидалось: {bytes(raw).hex()}')
    # Пишутся все 30 offset (включая Режим offset 0). Read-back может «врать» сразу после записи —
    # надёжнее свериться по дисплею ГМ.
    diff = [(i, raw[i], got[i]) for i in range(30) if raw[i] != got[i]]
    if not diff:
        print('РЕЗУЛЬТАТ: СОВПАЛО — настройки восстановлены ✔')
    else:
        print('РЕЗУЛЬТАТ: НЕ совпало ✘  расхождения (offset: ожид -> факт):')
        for i, a, b in diff:
            print(f'  [{i}] {a} -> {b}')
        sys.exit(1)


if __name__ == '__main__':
    main()

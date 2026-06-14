#!/usr/bin/env python3
"""
Temzit diagnostic report — автономный сборщик дампов и расшифровок.
Только стандартная библиотека Python 3. Не зависит от моста.

ЗАПУСК:
    python3 temzit_report.py --host 192.168.2.20
    python3 temzit_report.py --host 192.168.2.20 --port 333 --out my_report.txt
    python3 temzit_report.py --host 192.168.2.20 --out-dir /share/temzit
    (или задать TEMZIT_HOST / TEMZIT_PORT в окружении и просто: python3 temzit_report.py)

По умолчанию отчёт пишется НЕ в текущий каталог '/', а в первый записываемый из
/share/temzit, /config/temzit, ./temzit_reports (см. resolve_report_dir) — чтобы файл можно
было достать. Переопределить: --out-dir <каталог> или --out <полный путь>.

Что делает:
  - снимает два SYNC с паузой (видно, какие байты меняются во времени),
  - снимает один REQCFG (настройки),
  - для каждого ответа печатает: полный hex, таблицу offset/hex/dec, проверку CRC,
  - полную расшифровку SYNC и CFG; для спорных offset (3/6/8/19/23) показывает
    одновременно "трактовка кода" и "трактовка по протоколу", чтобы свериться.
  - пишет всё в текстовый файл — его и пришли на анализ.

БЕЗОПАСНОСТЬ: скрипт только ЧИТАЕТ (команды 0x30 и 0x34). Запись (0x35) не выполняется.
Соединение короткое, между запросами выдерживается пауза >=10 c (требование протокола).
"""
import os, sys, time, socket, argparse, datetime

CMD_SYNC = 0x30
CMD_REQCFG = 0x34
RESP_ACTUAL = 0x01
RESP_CONFIG = 0x02
EXPECTED_LEN = 64

P1_NAMES = {0: 'Стоп', 1: 'Нагрев', 2: 'Быстрый', 3: 'ТЭН', 4: 'Холод', 5: 'Внешний'}
TEN_MODE_NAMES = {0: '0%', 1: '30%', 2: '60%', 3: '100%'}
DHW_MODE_NAMES = {0: 'Выключен', 1: 'Только ТЭН в баке', 2: 'ТН 10%', 3: 'ТН 20%', 4: 'ТН 30%',
                  5: 'ТН 40%', 6: 'ТН 50%', 7: 'ТН 60%', 8: 'ТН 70%', 9: 'ТН 80%', 10: 'ТН 90%', 11: 'ТН 100%'}
COMP_LIMIT_NAMES = {0: 'Без ограничений', 1: '10%', 2: '20%', 3: '30%', 4: '40%', 5: '50%',
                    6: '55%', 7: '60%', 8: '70%', 9: '80%', 10: '90%'}
EXTERNAL_BOILER_NAMES = {0: 'Не использовать', 1: 'после I ступени', 2: 'после II ступени',
                         3: 'после III ступени', 4: 'только внешний'}
# Подтверждено по веб-интерфейсу: значение 5 = 'Электронный 4р'. Остальные значения пока не подтверждены замером.
FLOWMETER_TYPES = {0: 'unknown', 1: 'impulse_1l?', 2: 'impulse_10l?', 3: 'dual_channel?',
                   4: 'electronic?', 5: 'Электронный 4р', 6: 'reed_switch?'}
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

# Документированные имена параметров CFG (по протоколу ТЭМЗИТ), для таблицы.
CFG_PDF_NAMES = {
    0: 'Режим', 1: 'Тдома', 2: 'Тводы', 3: 'Инерция(hi)+РежимТЭНа(lo)',
    4: 'Тулич.вкл.ТЭНа', 5: 'Мин.Т использ.ККБ', 6: 'Дезинфекция(hi)+РежимГВС(lo)',
    7: 'Тгвс', 8: 'Внешний котёл', 9: 'Огранич.мощн.ККБ',
    10: 'WiFi(нельзя)', 11: 'WiFi(нельзя)', 12: 'WiFi(нельзя)', 13: 'WiFi(нельзя)',
    14: 'WiFi(нельзя)', 15: 'WiFi(нельзя)', 16: 'WiFi(нельзя)',
    17: 'Имп.электросчётчика', 18: 'Погодокомпенсация', 19: 'Тколлектор выкл(hi)+вкл(lo)',
    20: 'Режим реле ц.н.', 21: 'Макс.Тгвс от ККБ', 22: 'Тип расходомера',
    23: 'Перегрев СК действия(5б)+режим(3б)', 24: 'Т перегрева ТА от СК', 25: 'Тип ККБ1',
    26: '? недокументировано', 27: '? недокументировано', 28: '? недокументировано', 29: '? недокументировано',
}


def s8(v):
    return v if v < 128 else v - 256


def bcd(v):
    return (v >> 4) * 10 + (v & 0x0F)


def u16le(b, o):
    return b[o] | (b[o + 1] << 8)


def s16le(b, o):
    v = u16le(b, o)
    return v - 65536 if v >= 32768 else v


def u32le(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def checksum16(b):
    return sum(b) & 0xFFFF


def recv_until(s, n):
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


def query(host, port, timeout, payload):
    t0 = time.time()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(payload)
        data = recv_until(s, EXPECTED_LEN)
    return data, round((time.time() - t0) * 1000)


def query_retry(host, port, timeout, payload, retries, retry_delay, out):
    last = None
    for i in range(1, retries + 1):
        try:
            data, dt = query(host, port, timeout, payload)
            if len(data) >= EXPECTED_LEN:
                return data, dt
            last = f'short reply: {len(data)} bytes (hex={data.hex()})'
        except Exception as e:
            last = str(e)
        out(f'  попытка {i}/{retries} неуспешна: {last}; ждём {retry_delay}c')
        time.sleep(retry_delay)
    raise RuntimeError(f'не удалось получить ответ: {last}')


def dump_table(data, out):
    out(f'hex ({len(data)} bytes): {data.hex()}')
    crc_rx = int.from_bytes(data[62:64], 'little') if len(data) >= 64 else None
    crc_calc = checksum16(data[:62]) if len(data) >= 62 else None
    if crc_rx is not None:
        ok = 'MATCH' if crc_rx == crc_calc else 'FAIL'
        out(f'CRC16: rx={crc_rx} calc={crc_calc} -> {ok}')
    out('offset  hex  dec')
    for i, v in enumerate(data):
        out(f'  [{i:02d}]  {v:02x}  {v:4d}')


def decode_sync(data, out):
    p = data[2:62]
    out('--- SYNC decode ---')
    mc = u16le(p, 0)
    mc_name = STATE_MODE_NAMES[mc & 0xFF] if (mc & 0xFF) < len(STATE_MODE_NAMES) else '?'
    out(f'state/mode_code : {mc} ({mc_name})')
    out(f'schedule_no     : {u16le(p, 2)}')
    for nm, off in [('T_outdoor', 4), ('T_room', 6), ('T_supply', 8), ('T_return', 10),
                    ('T_freon_gas', 12), ('T_freon_liquid', 14), ('T_dhw', 16)]:
        out(f'{nm:15}: {s16le(p, off) / 10:6.1f} C   (s16={s16le(p, off)}, u16={u16le(p, off)})')
    out(f'flow_raw        : {u16le(p, 18)}')
    out(f'compressor_hz_1 : {p[22]}    compressor_hz_2: {p[23]}')
    out(f'heater_state    : {u16le(p, 24)}    dhw_heater_state: {u16le(p, 26)}')
    out(f'power_raw       : {u16le(p, 28)}  -> {u16le(p, 28) / 10:.1f} kW')
    al = u32le(p, 30)
    names = [n for bit, n in ALARM_BITS.items() if al & bit]
    out(f'alarm (u32)     : {al}  ({"ok" if not names else ",".join(names)})')
    out(f'fw_version      : {p[43]}.{p[44]}   (raw p[43]={p[43]} p[44]={p[44]})')
    out(f'active_sched_no : {p[45]}   active_sched_mode: {p[46]}')
    out(f'schedule echo   : 47={p[47]} 48={p[48]} 49={p[49]} 50={p[50]} 51={p[51]} 52={p[52]} 53={p[53]} 54={p[54]} 55={p[55]}')
    out(f'weekday raw     : {p[56]}')
    out(f'clock (BCD)     : {bcd(p[57]):02d}:{bcd(p[58]):02d}:{bcd(p[59]):02d}   (raw {p[57]:#04x} {p[58]:#04x} {p[59]:#04x})')
    out(f'clock (raw dec) : {p[57]}:{p[58]}:{p[59]}  <- если BCD-вариант выглядит верно, значит часы в BCD')


def decode_cfg(data, out):
    c = list(data[2:32])
    out('--- CFG decode (array = data[2:32], 30 bytes) ---')
    out('off  hex dec  параметр(протокол)                 расшифровка')
    for i, v in enumerate(c):
        name = CFG_PDF_NAMES.get(i, '')
        out(f' {i:2d}  {v:02x} {v:3d}  {name:34} ')
    out('')
    out('Ключевые поля (исправленная трактовка):')
    out(f'  Режим            off0 = {c[0]} ({P1_NAMES.get(c[0], "?")})')
    out(f'  Тдома уставка    off1 = {c[1]} C')
    out(f'  Тводы уставка    off2 = {c[2]} C')
    out(f'  off3 = 0x{c[3]:02x}: Инерция дома(hi) = {c[3] >> 4}, Режим ТЭНа(lo) = {c[3] & 0xF} ({TEN_MODE_NAMES.get(c[3] & 0xF, "?")})')
    out(f'  Тулич.вкл.ТЭНа   off4 = {s8(c[4])} C (signed)')
    out(f'  Мин.Т использ.ККБ off5 = {s8(c[5])} C (signed)')
    out(f'  off6 = 0x{c[6]:02x}: Дезинфекция(hi) = {c[6] >> 4}, Режим ГВС(lo) = {c[6] & 0xF} ({DHW_MODE_NAMES.get(c[6] & 0xF, "?")})')
    out(f'  Тгвс уставка     off7 = {c[7]} C')
    out(f'  Внешний котёл    off8 = {c[8]} ({EXTERNAL_BOILER_NAMES.get(c[8], "?")})')
    out(f'  Огранич.ККБ      off9 = {c[9]} ({COMP_LIMIT_NAMES.get(c[9], "?")})')
    out(f'  Имп.электросч.   off17= {c[17]}')
    out(f'  Погодокомп.      off18= {round(c[18] * 0.1, 1)}')
    out(f'  off19= 0x{c[19]:02x}: Тколлектор выкл(hi)={c[19] >> 4}, вкл(lo)={c[19] & 0xF}')
    out(f'  Режим реле ц.н.  off20= {c[20]}')
    out(f'  Макс.Тгвс от ККБ off21= {c[21]}')
    out(f'  Тип расходомера  off22= {c[22]} ({FLOWMETER_TYPES.get(c[22], "?")})')
    out(f'  off23= 0x{c[23]:02x}: Перегрев СК действия(5б)={c[23] >> 3}, режим СК(3б)={c[23] & 0x7}')
    out(f'  Т перегрева ТА   off24= {c[24]}')
    out(f'  Тип ККБ1         off25= {c[25]}')
    out(f'  WiFi (нельзя)    off10..16 = {c[10:17]}')
    out(f'  Недокументировано off26..29 = {c[26:30]}')
    out('')
    out('СВЕРКА спорных offset (старый код vs исправление):')
    out(f'  offset 6: код читал как BACKUP_TYPE цельным байтом = {c[6]} '
        f'(валиден 0-4? {"да" if c[6] <= 4 else "НЕТ -> признак ошибки"})')
    out(f'            исправлено: Режим ГВС = младший ниббл = {c[6] & 0xF} ({DHW_MODE_NAMES.get(c[6] & 0xF, "?")})')
    out(f'  offset 8: код читал как DHW_MODE = {c[8]} ({DHW_MODE_NAMES.get(c[8], "?")})')
    out(f'            исправлено: Внешний котёл = {c[8]} ({EXTERNAL_BOILER_NAMES.get(c[8], "?")})')
    out(f'  offset 3: код читал как aux_heater цельным байтом = {c[3]} '
        f'(64-67? {"да" if c[3] in (64, 65, 66, 67) else "НЕТ -> поедет при смене инерции"})')


def resolve_report_dir(preferred):
    """Первый каталог, в который реально удаётся писать (создаётся при необходимости).
    Чтобы отчёт не падал в '/', откуда его не достать. Порядок: предпочитаемый ->
    /share/temzit -> /config/temzit -> ./temzit_reports -> текущий каталог."""
    candidates = []
    for d in [preferred, '/share/temzit', '/config/temzit',
              os.path.join(os.getcwd(), 'temzit_reports')]:
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
    return os.getcwd()


def main():
    ap = argparse.ArgumentParser(description='Temzit diagnostic report (read-only)')
    ap.add_argument('--host', default=os.getenv('TEMZIT_HOST', ''))
    ap.add_argument('--port', type=int, default=int(os.getenv('TEMZIT_PORT', '333')))
    ap.add_argument('--timeout', type=int, default=int(os.getenv('TEMZIT_TIMEOUT', '15')))
    ap.add_argument('--retries', type=int, default=5, help='повторов на запрос (порт бывает занят выгрузкой статистики)')
    ap.add_argument('--retry-delay', type=int, default=15, help='пауза между повторами, c')
    ap.add_argument('--spacing', type=int, default=12, help='пауза между запросами, c (>=10 по протоколу)')
    ap.add_argument('--out', default=None, help='полный путь файла отчёта (переопределяет --out-dir)')
    ap.add_argument('--out-dir', default=os.getenv('TEMZIT_REPORT_DIR', ''),
                    help='каталог для отчёта (по умолчанию /share/temzit или иной записываемый, НЕ root)')
    args = ap.parse_args()

    if not args.host:
        print('Не задан --host (или TEMZIT_HOST). Пример: python3 temzit_report.py --host 192.168.2.20')
        sys.exit(2)

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.out:
        out_path = args.out
    else:
        report_dir = resolve_report_dir(args.out_dir or None)
        out_path = os.path.join(report_dir, f'temzit_report_{ts}.txt')
    lines = []

    def out(msg=''):
        lines.append(str(msg))
        print(msg)

    out('=' * 72)
    out(f'TEMZIT DIAGNOSTIC REPORT  {datetime.datetime.now().isoformat(timespec="seconds")}')
    out(f'host={args.host}:{args.port}  timeout={args.timeout}s  spacing={args.spacing}s')
    out('report tool v1  (read-only: 0x30 SYNC, 0x34 REQCFG)')
    out('=' * 72)

    try:
        out('\n##### SYNC sample #1 (0x30) #####')
        d1, dt1 = query_retry(args.host, args.port, args.timeout, bytes([CMD_SYNC, 0x00]),
                              args.retries, args.retry_delay, out)
        out(f'rtt={dt1} ms  type=0x{d1[0]:02x} ({"ACTUAL_STATE OK" if d1[0] == RESP_ACTUAL else "UNEXPECTED"})')
        dump_table(d1, out)
        decode_sync(d1, out)

        out(f'\n... пауза {args.spacing}c ...')
        time.sleep(args.spacing)

        out('\n##### SYNC sample #2 (0x30) #####')
        d2, dt2 = query_retry(args.host, args.port, args.timeout, bytes([CMD_SYNC, 0x00]),
                              args.retries, args.retry_delay, out)
        out(f'rtt={dt2} ms  type=0x{d2[0]:02x}')
        dump_table(d2, out)
        decode_sync(d2, out)

        out('\n##### SYNC diff (какие offset изменились между #1 и #2) #####')
        diffs = [(i, d1[i], d2[i]) for i in range(min(len(d1), len(d2))) if d1[i] != d2[i]]
        if not diffs:
            out('  изменений нет')
        for i, a, b in diffs:
            out(f'  [{i:02d}] {a:#04x}({a}) -> {b:#04x}({b})')

        out(f'\n... пауза {args.spacing}c ...')
        time.sleep(args.spacing)

        out('\n##### REQCFG (0x34) #####')
        dc, dtc = query_retry(args.host, args.port, args.timeout, bytes([CMD_REQCFG, 0x00]),
                              args.retries, args.retry_delay, out)
        out(f'rtt={dtc} ms  type=0x{dc[0]:02x} ({"CONFIG_MAIN OK" if dc[0] == RESP_CONFIG else "UNEXPECTED"})')
        dump_table(dc, out)
        decode_cfg(dc, out)

    except Exception as e:
        out(f'\nОШИБКА: {e}')

    out('\nDONE')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'\nОтчёт сохранён: {out_path}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Темзит MQTT Bridge v0.5.0
Параметры синхронизированы с веб-интерфейсом service.temzit.ru/settings
"""
import json, time, logging, os, requests
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("temzit")

# ── Загрузка конфига HA-аддона ─────────────────────────────────────────────
cfg_path = "/data/options.json"
with open(cfg_path) as f:
    CFG = json.load(f)

HOST     = CFG.get("temzit_host", "service.temzit.ru")
LOGIN    = CFG["temzit_login"]
PASSWORD = CFG["temzit_password"]
SERIAL   = CFG.get("temzit_serial", "")
PREFIX   = CFG.get("mqtt_prefix", "temzit")
INTERVAL = int(CFG.get("poll_interval", 60))
DEVICE_ID = SERIAL or "temzit_hp"

BASE_URL  = f"https://{HOST}"
SESSION   = requests.Session()

# ── Маппинг параметров из settings.html ────────────────────────────────────
# Структура: "имя_поля": {
#   "param": "Pxx" | "name",   # POST-имя в форме
#   "label": "...",             # человекочитаемое название
#   "type": "select"|"number"|"checkbox",
#   "options": {value: label},  # для select
#   "min/max/step": ...         # для number
# }

P1_OPTIONS = {
    "0": "Стоп",
    "1": "Нагрев",
    "2": "Быстрый нагрев",
    "3": "Только ТЭНы",
    "4": "Охлаждение",
    "5": "Внешний",
}

P2_OPTIONS = {"16": "нет", **{str(i): f"+{i}" for i in range(17, 31)}}

P4_OPTIONS = {
    "0": "Не использовать",
    "1": "Использовать до 30%",
    "2": "Использовать до 60%",
    "3": "Использовать до 100%",
}

P8_OPTIONS = {
    "0":  "Выключен",
    "1":  "Только ТЭН в баке",
    "2":  "ТН 10%",
    "3":  "ТН 20%",
    "4":  "ТН 30%",
    "5":  "ТН 40%",
    "6":  "ТН 50%",
    "7":  "ТН 60%",
    "8":  "ТН 70%",
    "9":  "ТН 80%",
    "10": "ТН 90%",
    "11": "ТН 100%",
}

P12_OPTIONS = {
    "0": "Не использовать",
    "1": "после I ступени",
    "2": "после II ступени",
    "3": "после III ступени",
    "4": "только внешний",
}

P14_OPTIONS = {str(i): str(round(i * 0.1, 1)) for i in range(11)}  # 0..10 -> 0.0..1.0

P85_OPTIONS = {
    "0": "нет",
    "1": "отопление и ГВС",
    "2": "только отопление",
    "3": "~отопление и ГВС",
    "4": "~только отопление",
}

P88_OPTIONS = {
    "0":  "Без ограничений",
    "1":  "10%",
    "2":  "20%",
    "3":  "30%",
    "4":  "40%",
    "5":  "50%",
    "6":  "55%",
    "7":  "60%",
    "8":  "70%",
    "9":  "80%",
    "10": "90%",
}

P91_OPTIONS = {
    "0": "Мех. 1л/имп",
    "1": "Мех. 10л/имп",
    "2": "Мех. сдвоенный 1л/имп",
    "3": "Мех. 1л/имп двухблочный",
    "4": "Мех. 10л/имп двухблочный",
    "5": "Электронный 4p",
    "6": "Электронный 2p",
}

TAMODE_OPTIONS = {
    "0": "выкл",
    "1": "СК",
    "2": "Ул",
    "3": "Дом",
}

P60_OPTIONS = {"0": "Выкл", "1": "Схема №1"}

GVS_DUAL_OPTIONS = {
    "0": "Только ККБ1",
    "1": "Только ККБ2",
    "2": "ККБ1+ККБ2 синхронно",
    "3": "ККБ2 ГВС || ККБ1 осн",
    "4": "ККБ1 ГВС || ККБ2 осн",
}

KKBDUAL_OPTIONS = {"0": "выкл", **{str(i): f"{i*10}%" for i in range(1, 11)}}

KKB_TYPES = {
    "-2": "авто", "-1": "нет",
    "0": "Mitsubishi бытовой", "1": "Mitsubishi полупром",
    "2": "Mitsubishi эффективный", "3": "Mitsubishi ZUBADAN",
    "4": "Haier 18BTU", "5": "Haier", "6": "Haier multy24",
    "7": "Haier superMAX60", "8": "Mitsubishi MUZ-SF50",
    "20": "AC18", "21": "AC24", "22": "AC36", "23": "AC48", "24": "AC60",
    "25": "07AC", "26": "06AC", "27": "06AM", "28": "10AC", "29": "10AM",
    "30": "14АС", "31": "14АС3Ф",
}

P71_OPTIONS = {"0": "Нет", "1": "Есть"}

# Полная схема управляемых параметров
PARAMS = {
    # ── Основные настройки ──────────────────────────────────────────────────
    "mode":          {"param": "P1",   "label": "Режим работы",              "type": "select",  "options": P1_OPTIONS},
    "t_home":        {"param": "P2",   "label": "Температура в помещении",   "type": "select",  "options": P2_OPTIONS},
    "t_water":       {"param": "P3",   "label": "Температура воды в системе","type": "number",  "min": 5,  "max": 55, "step": 1},
    "ta_mode":       {"param": "TAmode","label":"Режим ТА и выбор датчика",  "type": "select",  "options": TAMODE_OPTIONS},
    "hysteresis":    {"param": "P15",  "label": "Гистерезис отопления",      "type": "number",  "min": 0,  "max": 30, "step": 1},
    "aux_heater":    {"param": "P4",   "label": "Режим вспомогательного ТЭНа","type": "select", "options": P4_OPTIONS},
    "ext_heater":    {"param": "P12",  "label": "Внешний нагреватель",       "type": "select",  "options": P12_OPTIONS},
    "t_ten_on":      {"param": "P5",   "label": "Температура включения ТЭНа","type": "number",  "min": -25,"max": 25,  "step": 1},
    "t_comp_off":    {"param": "P6",   "label": "Температура выключения компрессора","type":"number","min":-25,"max":7,"step":1},
    "comp_limit":    {"param": "P88",  "label": "Ограничение мощности компрессора","type":"select","options":P88_OPTIONS},
    "inertia":       {"param": "P13",  "label": "Коэффициент инерции дома",  "type": "number",  "min": 0,  "max": 7,  "step": 1},
    "weather_comp":  {"param": "P14",  "label": "Погодокомпенсация",         "type": "select",  "options": P14_OPTIONS},
    "pump_mode":     {"param": "P85",  "label": "Управление циркуляционным насосом","type":"select","options":P85_OPTIONS},
    # ── ГВС ─────────────────────────────────────────────────────────────────
    "dhw_mode":      {"param": "P8",   "label": "Режим работы ГВС",          "type": "select",  "options": P8_OPTIONS},
    "t_dhw":         {"param": "P9",   "label": "Температура ГВС",           "type": "number",  "min": 20, "max": 70, "step": 1},
    "dhw_hysteresis":{"param": "P16",  "label": "Гистерезис ГВС",            "type": "number",  "min": 0,  "max": 30, "step": 1},
    "t_dhw_max_hp":  {"param": "P10",  "label": "Макс. температура нагрева от ТН","type":"number","min":35,"max":50,"step":1},
    "dhw_disinfect": {"param": "P11b0","label": "Дезинфекция ГВС",           "type": "checkbox"},
    "dhw_defrost":   {"param": "P11b2","label": "Разморозка в БКН",          "type": "checkbox"},
    "dhw_t_ul":      {"param": "HardVersion2b1","label":"Учитывать Тул (ГВС)","type":"select","options":{"0":"да","1":"нет"}},
    "dhw_dual":      {"param": "GVSDualMode","label":"Выбор ККБ (для ГМ2)",  "type": "select",  "options": GVS_DUAL_OPTIONS},
    # ── Солнечный коллектор ─────────────────────────────────────────────────
    "solar_mode":    {"param": "P60",  "label": "Режим солнечного коллектора","type": "select", "options": P60_OPTIONS},
    "solar_t_on":    {"param": "P61",  "label": "Т включения насоса СК",     "type": "number",  "min": 5,  "max": 20, "step": 1},
    "solar_t_off":   {"param": "P62",  "label": "Т выключения насоса СК",    "type": "number",  "min": 2,  "max": 12, "step": 1},
    "solar_t_overheat":{"param":"P64", "label": "Температура перегрева БКН", "type": "number",  "min": 50, "max": 90, "step": 1},
    # ── Связь ───────────────────────────────────────────────────────────────
    "poll_server":   {"param": "P72",  "label": "Период обращения к серверу","type": "number",  "min": 1,  "max": 99, "step": 1},
    "wifi_thermo":   {"param": "P71",  "label": "WiFi термометр",            "type": "select",  "options": P71_OPTIONS},
    # ── Конфигурация ────────────────────────────────────────────────────────
    "kkb1_type":     {"param": "KKB1Type","label":"Тип ККБ1",                "type": "select",  "options": KKB_TYPES},
    "kkb2_type":     {"param": "KKB2Type","label":"Тип ККБ2",                "type": "select",  "options": KKB_TYPES},
    "kkb_dual_lim":  {"param": "KKBDualLim","label":"Двухблочный режим (начало включения ККБ2)","type":"select","options":KKBDUAL_OPTIONS},
    "flow_sensor":   {"param": "P91",  "label": "Датчик протока",            "type": "select",  "options": P91_OPTIONS},
    "glycol":        {"param": "HardVersion2b6","label":"Гликоль",           "type": "checkbox"},
    "energy_meter":  {"param": "P87",  "label": "Счётчик электрический (имп/кВт)","type":"number","min":0,"max":25599,"step":100},
}

# ── MQTT ─────────────────────────────────────────────────────────────────────
mqttc = mqtt.Client(client_id=f"temzit_bridge_{DEVICE_ID}")
if CFG.get("mqtt_user"):
    mqttc.username_pw_set(CFG["mqtt_user"], CFG.get("mqtt_password", ""))

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": "Темзит тепловой насос",
    "manufacturer": "ТЭМЗИТ",
    "model": "WiFi Control",
    "sw_version": "0.5.0",
}


def publish(topic, payload, retain=True):
    mqttc.publish(topic, json.dumps(payload) if isinstance(payload, dict) else payload, retain=retain)


def ha_discovery():
    """Публикует конфигурации MQTT Discovery для всех параметров."""
    base = PREFIX

    # SELECT-сущности
    for key, p in PARAMS.items():
        if p["type"] != "select":
            continue
        cfg = {
            "name": p["label"],
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic":   f"{base}/{DEVICE_ID}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": f"{base}/{DEVICE_ID}/set/{key}",
            "options": list(p["options"].values()),
            "device": DEVICE_INFO,
        }
        publish(f"homeassistant/select/{DEVICE_ID}/{key}/config", cfg)

    # NUMBER-сущности
    for key, p in PARAMS.items():
        if p["type"] != "number":
            continue
        cfg = {
            "name": p["label"],
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic":   f"{base}/{DEVICE_ID}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": f"{base}/{DEVICE_ID}/set/{key}",
            "min": p["min"], "max": p["max"], "step": p["step"],
            "mode": "slider",
            "device": DEVICE_INFO,
        }
        if "t_" in key or key in ("hysteresis","dhw_hysteresis","solar_t_on","solar_t_off","solar_t_overheat"):
            cfg["unit_of_measurement"] = "°C"
            cfg["device_class"] = "temperature"
        publish(f"homeassistant/number/{DEVICE_ID}/{key}/config", cfg)

    # SWITCH-сущности (checkbox)
    for key, p in PARAMS.items():
        if p["type"] != "checkbox":
            continue
        cfg = {
            "name": p["label"],
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic":   f"{base}/{DEVICE_ID}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": f"{base}/{DEVICE_ID}/set/{key}",
            "payload_on": "1", "payload_off": "0",
            "state_on": "1",   "state_off": "0",
            "device": DEVICE_INFO,
        }
        publish(f"homeassistant/switch/{DEVICE_ID}/{key}/config", cfg)

    # Сенсоры (только чтение — диагностика)
    sensors = {
        "t_outdoor":  ("Температура улица",     "°C", "temperature"),
        "t_indoor":   ("Температура дом",        "°C", "temperature"),
        "t_supply":   ("Температура подача",     "°C", "temperature"),
        "t_return":   ("Температура обратка",    "°C", "temperature"),
        "t_dhw_curr": ("Температура ГВС текущая","°C", "temperature"),
        "t_freon_gas":("Температура фреон газ",  "°C", "temperature"),
        "t_freon_liq":("Температура фреон жидк", "°C", "temperature"),
        "freq_comp1": ("Частота компрессора ККБ1","Гц", None),
        "freq_comp2": ("Частота компрессора ККБ2","Гц", None),
        "flow_rate":  ("Проток теплоносителя",   "л/мин", None),
        "power_kw":   ("Мощность",               "кВт", "power"),
        "error_code": ("Код ошибки",             None,  None),
        "status_text":("Статус",                 None,  None),
    }
    for key, (label, unit, dev_class) in sensors.items():
        cfg = {
            "name": label,
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic":    f"{base}/{DEVICE_ID}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "device": DEVICE_INFO,
        }
        if unit:
            cfg["unit_of_measurement"] = unit
        if dev_class:
            cfg["device_class"] = dev_class
        publish(f"homeassistant/sensor/{DEVICE_ID}/{key}/config", cfg)

    log.info("MQTT Discovery опубликован")


def login():
    r = SESSION.post(f"{BASE_URL}/login", data={"email": LOGIN, "password": PASSWORD}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Авторизация не удалась: {r.status_code}")
    log.info("Авторизация успешна")


def fetch_state() -> dict:
    """Получить текущие параметры с /settings и /status."""
    raw_settings = SESSION.get(f"{BASE_URL}/settings", timeout=10).text
    raw_status   = SESSION.get(f"{BASE_URL}/status",   timeout=10).text
    state = parse_settings(raw_settings)
    state.update(parse_status(raw_status))
    return state


def _extract_selected(html: str, field: str) -> str | None:
    """Вытащить значение selected option для поля field."""
    import re
    m = re.search(
        r'<select[^>]+name="' + re.escape(field) + r'"[^>]*>(.*?)</select>',
        html, re.DOTALL
    )
    if not m:
        return None
    sel = re.search(r'<option value="([^"]*)"[^>]*selected', m.group(1))
    return sel.group(1) if sel else None


def _extract_checkbox(html: str, field: str) -> str:
    import re
    m = re.search(r'<input[^>]+name="' + re.escape(field) + r'"[^>]*/>', html)
    if m and "checked" in m.group(0):
        return "1"
    return "0"


def _extract_number(html: str, field: str) -> str | None:
    import re
    m = re.search(r'<input[^>]+name="' + re.escape(field) + r'"[^>]+value="([^"]+)"', html)
    return m.group(1) if m else None


def parse_settings(html: str) -> dict:
    state = {}
    for key, p in PARAMS.items():
        field = p["param"]
        if p["type"] == "select":
            raw = _extract_selected(html, field)
            if raw is not None:
                # Перевести числовой код в текстовое значение для HA
                state[key] = p["options"].get(raw, raw)
        elif p["type"] == "number":
            raw = _extract_selected(html, field) or _extract_number(html, field)
            if raw is not None:
                state[key] = raw
        elif p["type"] == "checkbox":
            state[key] = _extract_checkbox(html, field)
    return state


def parse_status(html: str) -> dict:
    """Парсим страницу /status — берём диагностические датчики."""
    import re
    state = {}
    # Паттерн: ищем строки вида <td>Имя</td><td>Значение</td>
    rows = re.findall(r'<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
    mapping = {
        "Тул":        "t_outdoor",
        "Тдом":       "t_indoor",
        "Тподача":    "t_supply",
        "Тобратка":   "t_return",
        "Тгвс":       "t_dhw_curr",
        "Тфреон газ": "t_freon_gas",
        "Тфреон жидк":"t_freon_liq",
        "ККБ1 Гц":    "freq_comp1",
        "ККБ2 Гц":    "freq_comp2",
        "Проток":     "flow_rate",
        "Мощность":   "power_kw",
        "Авария":     "error_code",
        "Состояние":  "status_text",
    }
    for name, value in rows:
        name_clean = re.sub(r'<[^>]+>', '', name).strip()
        val_clean  = re.sub(r'<[^>]+>', '', value).strip()
        if name_clean in mapping:
            state[mapping[name_clean]] = val_clean
    return state


def send_command(key: str, value: str):
    """Отправить команду на /settings/update."""
    p = PARAMS.get(key)
    if not p:
        log.warning(f"Неизвестный параметр: {key}")
        return

    # Для select: перевести текстовое значение обратно в числовой код
    if p["type"] == "select":
        inv = {v: k for k, v in p["options"].items()}
        code = inv.get(value, value)
    elif p["type"] == "checkbox":
        code = "1" if value in ("1", "true", "on", "ON") else "0"
    else:
        code = value

    data = {p["param"]: code}
    r = SESSION.post(f"{BASE_URL}/settings/update", data=data, timeout=10)
    if r.status_code == 200:
        log.info(f"Команда OK: {key}={value} -> {p['param']}={code}")
    else:
        log.error(f"Ошибка команды {key}: HTTP {r.status_code}")


# ── MQTT callbacks ────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT подключён")
        client.subscribe(f"{PREFIX}/{DEVICE_ID}/set/#")
        ha_discovery()
    else:
        log.error(f"MQTT ошибка подключения: rc={rc}")


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8").strip()
    # темзит/serial/set/key
    parts = topic.split("/")
    if len(parts) >= 4 and parts[-2] == "set":
        key = parts[-1]
        log.info(f"Получена команда: {key}={payload}")
        send_command(key, payload)


mqttc.on_connect = on_connect
mqttc.on_message = on_message


def main():
    log.info("Темзит MQTT Bridge v0.5.0 запускается")
    login()
    mqttc.connect(CFG["mqtt_host"], int(CFG.get("mqtt_port", 1883)), 60)
    mqttc.loop_start()

    while True:
        try:
            state = fetch_state()
            mqttc.publish(
                f"{PREFIX}/{DEVICE_ID}/state",
                json.dumps(state, ensure_ascii=False),
                retain=True
            )
            log.info(f"Состояние опубликовано: {len(state)} параметров")
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 401:
                log.warning("Сессия устарела — повторная авторизация")
                login()
            else:
                log.error(f"HTTP ошибка: {e}")
        except Exception as e:
            log.error(f"Ошибка опроса: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

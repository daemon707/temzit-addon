# Temzit MQTT Bridge add-on

Home Assistant add-on for local integration of Temzit heat pumps via the official local TCP protocol and MQTT Discovery.

## What it does

- Connects to Temzit over TCP port 333.
- Polls `SYNC` and `REQCFG`.
- Publishes telemetry to MQTT.
- Accepts MQTT set commands and writes configuration back using `CFG`.
- Creates MQTT Discovery entities in Home Assistant.

## Required settings

- `temzit_host`: IP of the Temzit controller.
- `mqtt_host`: MQTT broker IP.
- `mqtt_user` / `mqtt_pass`: broker credentials.

## Notes

- Polling should stay conservative; the Temzit protocol document recommends not querying too often.
- Frequent writes are discouraged because config is persisted in non-volatile memory.

#!/usr/bin/with-contenv bashio
set -e

export TEMZIT_HOST="$(bashio::config 'temzit_host')"
export TEMZIT_PORT="$(bashio::config 'temzit_port')"
export TEMZIT_TIMEOUT="$(bashio::config 'temzit_timeout')"
export TEMZIT_SYNC_INTERVAL="$(bashio::config 'temzit_sync_interval')"
export TEMZIT_RETRY_DELAY="$(bashio::config 'temzit_retry_delay')"

export MQTT_HOST="$(bashio::config 'mqtt_host')"
export MQTT_PORT="$(bashio::config 'mqtt_port')"
export MQTT_USER="$(bashio::config 'mqtt_user')"
export MQTT_PASS="$(bashio::config 'mqtt_pass')"
export MQTT_PREFIX="$(bashio::config 'mqtt_prefix')"
export MQTT_DISCOVERY_PREFIX="$(bashio::config 'mqtt_discovery_prefix')"
export MQTT_CLIENT_ID="$(bashio::config 'mqtt_client_id')"

bashio::log.info "Starting Temzit MQTT Bridge (read-only)"
bashio::log.info "Temzit host: ${TEMZIT_HOST}:${TEMZIT_PORT}"
bashio::log.info "MQTT host: ${MQTT_HOST}:${MQTT_PORT}"

exec python3 /usr/bin/temzit_mqtt_bridge.py

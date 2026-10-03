ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.19
FROM ${BUILD_FROM}
RUN apk add --no-cache python3 py3-paho-mqtt bash netcat-openbsd ca-certificates
COPY rootfs /
RUN chmod a+x /usr/bin/run.sh /usr/bin/temzit_mqtt_bridge.py /usr/bin/temzit_cloud.py
CMD [ "/usr/bin/run.sh" ]

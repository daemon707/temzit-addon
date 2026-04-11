ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-paho-mqtt bash

COPY rootfs /
RUN chmod a+x /usr/bin/run.sh /usr/bin/temzit_mqtt_bridge.py

CMD [ "/usr/bin/run.sh" ]

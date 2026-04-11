ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip bash \
    && pip3 install --no-cache-dir paho-mqtt

COPY rootfs /
RUN chmod a+x /usr/bin/run.sh /usr/bin/temzit_mqtt_bridge.py
CMD [ "/usr/bin/run.sh" ]

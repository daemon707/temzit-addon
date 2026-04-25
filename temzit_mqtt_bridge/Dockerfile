ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3 py3-pip && pip3 install --no-cache-dir paho-mqtt requests
COPY run.py /
CMD ["python3", "/run.py"]

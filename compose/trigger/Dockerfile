FROM python:3.13-slim-trixie

LABEL pg-stack="true"

RUN pip install --no-cache-dir httpx>=0.27.0 \
 && useradd -r -s /sbin/nologin trigger

COPY trigger.py /app/trigger.py

USER trigger
WORKDIR /app

ENTRYPOINT ["python", "/app/trigger.py"]

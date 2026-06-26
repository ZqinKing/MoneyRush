FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY collector/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

COPY collector /app/collector
RUN mkdir -p /root/.mootdx && cp /app/collector/mootdx_config.json /root/.mootdx/config.json

WORKDIR /app

CMD ["python", "-m", "collector.main"]

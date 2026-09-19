FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATABASE_PATH=/app/data/blends.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data

EXPOSE 8000

# 启动时自动完成建库建表（见 app/main.py 的 lifespan）
CMD uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}

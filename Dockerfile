FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DIR2CHAT_CONFIG_DIR=/config \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY VERSION ./
COPY app ./app

EXPOSE 8899

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8899/healthz')"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8899"]

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /config /config/logs && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /config

USER appuser

ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

ENTRYPOINT ["python", "-m", "televisarr.main"]
CMD []
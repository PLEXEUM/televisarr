FROM python:3.11-slim

WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create config directory
RUN mkdir -p /config

# Run as non-root user
RUN adduser --system --uid 1000 appuser && \
    chown -R appuser:appuser /app /config
USER appuser

ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

ENTRYPOINT ["python", "-m", "televisarr.main"]
CMD []
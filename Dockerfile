FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create directory for config and logs
RUN mkdir -p /config /config/logs

# Create a non-root user to run the application
RUN addgroup --system --gid 1000 appuser && \
    adduser --system --uid 1000 --gid 1000 appuser

# Change ownership of the application and config directories
RUN chown -R appuser:appuser /app /config

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO

# Default command
ENTRYPOINT ["python", "-m", "televisarr"]

# Default arguments (can be overridden)
CMD []
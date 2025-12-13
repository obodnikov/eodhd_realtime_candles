FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Copy supervisord configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create necessary directories
RUN mkdir -p /data /var/log/supervisor

# Set environment defaults
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8765
ENV DATABASE_PATH=/data/candles.db
ENV LOG_LEVEL=INFO

# Admin UI defaults
ENV ADMIN_ENABLED=true
ENV ADMIN_HOST=127.0.0.1
ENV ADMIN_PORT=5000
ENV ADMIN_API_URL=http://localhost:8765

# Expose ports
EXPOSE 8765 5000

# Health check with timeout to prevent hanging requests
HEALTHCHECK --interval=90s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request, socket; socket.setdefaulttimeout(3); urllib.request.urlopen('http://localhost:8765/health')" || exit 1

# Run supervisord to manage both services
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

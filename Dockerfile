FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Create data directory
RUN mkdir -p /data

# Set environment defaults
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8765
ENV DATABASE_PATH=/data/candles.db
ENV LOG_LEVEL=INFO

# Expose port
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

# Run the service
CMD ["python", "-m", "src.main"]

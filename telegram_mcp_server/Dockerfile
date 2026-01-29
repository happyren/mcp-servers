FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install dependencies
RUN pip install --no-cache-dir -e .

# Create directory for queue file
RUN mkdir -p /root/.local/share/telegram_mcp_server

# Set the entry point
ENTRYPOINT ["telegram-mcp-server"]

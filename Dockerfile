# The whole app in one image: the built frontend, served by the Python backend.
# Build:  docker build -t reading-companion .
# With Claude Code inside (only for the owner's own subscription):  --build-arg CLAUDE_CODE=1

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data HF_HOME=/data/models FRONTEND_DIR=/app/frontend
WORKDIR /app
# CPU-only torch: about 200 MB instead of several GB with CUDA. Cloud hosts rarely have a GPU.
# Embedding a book then takes minutes instead of seconds; the reader can read meanwhile, only chat waits.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt backend/
RUN pip install -r backend/requirements.txt

ARG CLAUDE_CODE=0
RUN if [ "$CLAUDE_CODE" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/* && \
      npm install -g @anthropic-ai/claude-code; \
    fi

COPY backend/ backend/
COPY --from=web /web/dist frontend/
RUN useradd --create-home --uid 1000 reader && mkdir -p /data && chown reader /data
USER reader
WORKDIR /app/backend
EXPOSE 8000
# --proxy-headers: behind a host's proxy, the reader's real address comes from X-Forwarded-For.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

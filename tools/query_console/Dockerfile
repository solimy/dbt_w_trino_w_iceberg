FROM node:24-bookworm-slim AS frontend
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY frontend/ ./frontend/
RUN sh frontend/build.sh

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app.py .
COPY --from=frontend /build/dist /app/static
USER 10001:10001
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:8080", "app:create_app()"]

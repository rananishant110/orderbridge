FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV OB_STORAGE_DIR=/app/storage
RUN mkdir -p /app/storage/templates /app/storage/runs

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "orderbridge.main:app", "--host", "0.0.0.0", "--port", "8000"]

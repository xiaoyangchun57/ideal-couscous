FROM node:22-alpine AS frontend-builder

WORKDIR /build/react-vite
COPY react-vite/package.json react-vite/package-lock.json ./
RUN npm ci
COPY react-vite/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY --from=frontend-builder /build/frontend/v2/ /app/frontend/v2/

RUN mkdir -p /app/backend/data /app/frontend/uploads

EXPOSE 5000

CMD ["python", "app.py"]

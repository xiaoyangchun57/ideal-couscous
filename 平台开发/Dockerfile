cd /opt/water-monitor/平台开发
cat > Dockerfile << 'DOCKERFILE_END'
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

COPY frontend/ frontend/

EXPOSE 5005

CMD ["python", "app.py"]
DOCKERFILE_END

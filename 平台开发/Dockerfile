cd /opt/water-monitor/平台开发
cat > Dockerfile << 'EOF'
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个后端目录（解决漏拷文件的问题）
COPY backend/ ./

# 复制前端
COPY frontend/ frontend/

EXPOSE 5005

CMD ["python", "app.py"]
EOF

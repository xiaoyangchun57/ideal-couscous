FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY backend/app.py .
COPY backend/seed_demo.py .
COPY backend/sl651_server.py .
COPY backend/sl651_parser.py .
COPY backend/sl651_mapping.json .

# 复制数据库
COPY backend/data/ data/

# 复制前端构建产物
COPY frontend/ frontend/

# 端口
EXPOSE 5005

# 启动
CMD ["python", "app.py"]

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
# 前端构建产物由构建阶段或 CI 生成后拷入;此处若不存在则后端仅提供 API
COPY frontend/dist /app/frontend/dist

WORKDIR /app/backend
EXPOSE 8000
VOLUME ["/app/data"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

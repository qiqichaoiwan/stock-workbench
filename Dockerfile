FROM node:24-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime-base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
# redis-py has no network side effect at import time. Installing the optional
# client in the image lets the Compose Redis profile work; the application
# still defaults to memory mode and remains usable when no Redis server exists.
COPY backend/requirements-marketdata-redis.txt /app/backend/requirements-marketdata-redis.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-marketdata-redis.txt

# Keep the API image as the main/default target for deployments that do not
# enable the marketdata profile.  Defining it before the optional worker target
# also lets classic Docker builds of `--target app` skip the Futu SDK install.
FROM runtime-base AS app
COPY backend /app/backend
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist
WORKDIR /app/backend
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# The Futu worker is a separate image target so the normal API image does not
# install the optional broker SDK or open a second OpenD connection.  Compose
# enables this target only with `--profile marketdata`.
FROM runtime-base AS futu-worker
COPY backend/requirements-futu.txt /app/backend/requirements-futu.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-futu.txt
COPY backend /app/backend
WORKDIR /app/backend
CMD ["python", "scripts/marketdata_worker.py"]

# Keep `docker build .` on the API image even though futu-worker is declared
# last for the explicit Compose target above.
FROM app AS default

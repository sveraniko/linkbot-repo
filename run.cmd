@echo off
set COMPOSE_PROJECT_NAME=linkbot

echo Starting base services...
docker compose --env-file .env up -d db minio

echo Waiting for MinIO to be ready...
:wait_minio
curl -f http://localhost:9100/minio/health/ready >nul 2>&1
if errorlevel 1 (
    timeout /t 2 >nul
    goto wait_minio
)

echo Running migrations...
docker compose --env-file .env run --rm bot alembic upgrade head

echo Starting bot...
docker compose --env-file .env up --build -d bot

echo Showing bot logs...
docker compose logs -f bot

pause

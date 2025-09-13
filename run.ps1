$ErrorActionPreference = "Stop"
$env:COMPOSE_PROJECT_NAME = "project-memory-bot"

function Wait-Http($url, $timeoutSec = 120) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
    try {
      $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
    } catch { Start-Sleep -Seconds 2 }
  }
  return $false
}

# Auto-detect MinIO public URL from .env
$minioUrl = "http://localhost:9100"
if (Test-Path ".env") {
  $envContent = Get-Content ".env" -Raw
  if ($envContent -match "MINIO_PUBLIC_URL=([^\r\n]+)") {
    $minioUrl = $matches[1]
    Write-Host "Detected MinIO public URL: $minioUrl"
  }
}

# 1) Поднять базу и MinIO
docker compose --env-file .env up -d db minio

# 2) Подождать готовность MinIO по опубликованному порту
$healthUrl = "$minioUrl/minio/health/ready"
Write-Host "Waiting for MinIO health check: $healthUrl"
if (-not (Wait-Http $healthUrl 180)) {
  Write-Error "MinIO не готов ($healthUrl)."
  exit 1
}
Write-Host "MinIO is ready!"

# 3) Прогнать миграции ОДНОКРАТНО
docker compose --env-file .env run --rm bot alembic upgrade head

# 4) Запустить бота
docker compose --env-file .env up --build -d bot

# 5) Показать логи бота
docker compose logs -f bot


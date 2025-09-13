# Mini-script to wait for MinIO service before starting the bot
import os, socket, time
from urllib.parse import urlparse

endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
u = urlparse(endpoint)
host = u.hostname or "minio"
port = u.port or 9000

print(f"Waiting for MinIO at {host}:{port}...")

deadline = time.time() + 180
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"MinIO is ready at {host}:{port}")
            break
    except OSError:
        time.sleep(2)
else:
    raise SystemExit(f"MinIO not reachable at {host}:{port} within timeout")
SHELL := /usr/bin/env bash

.PHONY: up shell db test

up:
	docker compose up -d --build

shell:
	docker compose exec app bash

db:
	docker compose exec app alembic upgrade head

test:
	# basic smoke/e2e placeholder
	docker compose exec app python - <<'PY'
print('SMOKE: starting...')
# add lightweight imports to ensure app boots
try:
    import app.main as m
    print('Import ok')
except Exception as e:
    print('Import failed:', e)
    raise
print('SMOKE: ok')
PY

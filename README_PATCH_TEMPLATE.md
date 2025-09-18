Patch application template

1) Save diff as patch file
- Example: save this section into patch.diff

2) Apply patch
- Linux/macOS
  git apply --index patch.diff
  git commit -m "Apply patch"

- Windows PowerShell
  git.exe apply --index patch.diff
  git.exe commit -m "Apply patch"

3) If conflicts occur
- Manually edit files, then:
  git add -A
  git commit -m "Resolve patch conflicts"

Notes
- Do not modify .env* or docker-compose*.yml. Guarded by hook.
- Migrations only forward; run inside container:
  make db
- Start stack:
  make up
- Run tests:
  make test

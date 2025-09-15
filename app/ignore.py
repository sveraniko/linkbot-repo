from __future__ import annotations
from pathlib import Path
from typing import Iterable
from pathspec import PathSpec

DEFAULT_PMIGNORE = """
# папки
node_modules/
dist/
build/
.cache/
.venv/
venv/
.git/
__pycache__/
# файлы
*.png
*.jpg
*.jpeg
*.gif
*.webp
*.mp4
*.pdf
*.zip
*.tar
*.log
"""

def load_pmignore(root: Path, extra_patterns: Iterable[str] | None = None) -> PathSpec:
    patts: list[str] = []
    pm = root / ".pmignore"
    if pm.exists():
        patts += pm.read_text(encoding="utf-8", errors="ignore").splitlines()
    else:
        patts += DEFAULT_PMIGNORE.splitlines()
    if extra_patterns:
        patts += list(extra_patterns)
    return PathSpec.from_lines("gitwildmatch", patts)

def should_ignore(filename: str, root: Path | None = None) -> bool:
    """
    Check if a file should be ignored based on .pmignore patterns.
    If root is not provided, uses default patterns only.
    """
    if root and root.exists():
        spec = load_pmignore(root)
    else:
        # Use default patterns only
        spec = PathSpec.from_lines("gitwildmatch", DEFAULT_PMIGNORE.splitlines())
    
    return spec.match_file(filename)

def iter_text_files(root: Path, spec: PathSpec):
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if spec.match_file(rel):
                continue
            # простая эвристика «текст/нет»
            try:
                data = p.read_bytes()
                data.decode("utf-8")
            except Exception:
                continue
            yield rel, data.decode("utf-8", errors="ignore")
"""Project-relative paths that do not depend on the process working directory."""

from __future__ import annotations

from pathlib import Path


# ``public/util`` is two levels below the repository root.  Resolving from the
# module location keeps every process (including child camera processes) in the
# same project tree even when PyCharm or a service starts with another cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    """Return *path* unchanged when absolute, otherwise anchor it at project root."""

    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


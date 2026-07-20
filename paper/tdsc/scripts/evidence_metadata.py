"""Small helpers for raw-result reproducibility metadata."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_root, text=True).strip()


def git_metadata(repo_root: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "commit": os.environ.get("GIT_COMMIT", "unknown"),
        "full_commit": os.environ.get("GIT_COMMIT", "unknown"),
        "dirty": None,
        "status_short": "",
        "diff_hash": "",
        "diff_stat": "",
    }
    try:
        full_commit = _git(repo_root, "rev-parse", "HEAD")
        metadata["full_commit"] = full_commit
        metadata["commit"] = os.environ.get("GIT_COMMIT") or full_commit[:7]
        status = _git(repo_root, "status", "--short")
        diff = _git(repo_root, "diff", "--binary")
        metadata["status_short"] = status
        metadata["dirty"] = bool(status)
        metadata["diff_hash"] = hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else ""
        metadata["diff_stat"] = _git(repo_root, "diff", "--stat")
    except Exception as exc:
        metadata["metadata_error"] = str(exc).splitlines()[0]
    return metadata

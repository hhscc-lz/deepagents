"""Session utilities for per-user workspace isolation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from langgraph.config import get_config

_USER_ID_RE = re.compile(r"^[A-Za-z0-9\-_.@+:~]+$")


def _read_user_id(value: Any) -> str | None:
    """Normalize a candidate user_id value."""
    if not isinstance(value, str):
        return None
    user_id = value.strip()
    if not user_id:
        return None
    if not _USER_ID_RE.match(user_id):
        msg = (
            f"Invalid user_id: {user_id!r}. "
            "Only letters, digits, '-', '_', '.', '@', '+', ':', '~' are allowed."
        )
        raise RuntimeError(msg)
    return user_id


def _read_user_id_from_mapping(mapping: Any) -> str | None:
    """Extract user_id from a mapping-like object."""
    if not isinstance(mapping, dict):
        return None
    return _read_user_id(mapping.get("user_id"))


def _read_user_id_from_config(config: Any) -> str | None:
    """Extract user_id from RunnableConfig."""
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    return _read_user_id_from_mapping(configurable)


def resolve_session_user(runtime: Any | None = None) -> str:
    """Resolve user_id from runtime context/config, with local fallback."""
    if runtime is not None:
        user_id = _read_user_id_from_mapping(getattr(runtime, "context", None))
        if user_id:
            return user_id

        user_id = _read_user_id_from_config(getattr(runtime, "config", None))
        if user_id:
            return user_id

    # 兜底：部分工具调用场景可从 langgraph 运行时配置读取 configurable.user_id。
    try:
        cfg = get_config()
    except Exception:  # noqa: BLE001
        cfg = None
    user_id = _read_user_id_from_config(cfg)
    if user_id:
        return user_id

    # 本地调试兜底，不建议在多用户 Web 场景使用。
    env_user = _read_user_id(os.environ.get("MINXIN_USER"))
    if env_user:
        return env_user

    msg = (
        "Missing user_id. Pass user_id via runtime context "
        "or config.configurable.user_id."
    )
    raise RuntimeError(msg)


def get_workspace_dir(user_id: str) -> Path:
    """Build workspace directory path for a specific user."""
    # return Path("/minxinagents") / user_id / "workspace"
    return (
        Path(
            "/Users/lzzzii/home/hhscc-lz-workspace/minxinagents/services/minxin-analysis-agent/sessions"
        )
        / user_id
        / "workspace"
    )


def ensure_workspace_dirs(user_id: str) -> tuple[Path, Path, Path]:
    """Ensure workspace, memory file, and skills dir exist."""
    workspace_dir = get_workspace_dir(user_id)
    memory_file = workspace_dir / "AGENTS.md"
    skills_dir = workspace_dir / "skills"

    workspace_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    memory_file.touch(exist_ok=True)
    return workspace_dir, memory_file, skills_dir


def is_path_inside_workspace(path: Path, workspace_dir: Path) -> bool:
    """Check whether a path stays inside workspace_dir."""
    resolved_path = path.resolve()
    resolved_workspace = workspace_dir.resolve()
    return resolved_path == resolved_workspace or str(resolved_path).startswith(
        str(resolved_workspace) + os.sep
    )

"""Minxin analysis agent backend entrypoint with fixed configuration."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from analysis_agent.prompts import SYSTEM_PROMPT_TEMPLATE
from analysis_agent.session import ensure_workspace_dirs, resolve_session_user
from analysis_agent.tools import export_data, upload_file
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware import MemoryMiddleware, SkillsMiddleware
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime
from typing_extensions import TypedDict

# 可选：如果安装了 `python-dotenv`，就自动加载 `.env`。
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def require_env(name: str) -> str:
    """Read a required environment variable."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# 固定配置：模型参数（由 .env 提供）
MODEL_PROVIDER = require_env("MINXIN_MODEL_PROVIDER")
MODEL_NAME = require_env("MINXIN_MODEL_NAME")


class Context(TypedDict, total=False):
    """Runtime context for per-request user isolation."""

    user_id: str


def create_shell_backend(runtime: ToolRuntime[Any, Any]) -> LocalShellBackend:
    """Create LocalShellBackend bound to the current request user workspace."""
    user_id = resolve_session_user(runtime)
    workspace_dir, _, _ = ensure_workspace_dirs(user_id)

    # 为 execute 工具透传会话用户，便于 shell 子进程读取身份信息。
    shell_env = os.environ.copy()
    shell_env["MINXIN_USER"] = user_id
    return LocalShellBackend(
        root_dir=workspace_dir,
        inherit_env=True,
        env=shell_env,
        virtual_mode=True,
    )


def create_filesystem_backend(runtime: ToolRuntime[Any, Any]) -> FilesystemBackend:
    """Create FilesystemBackend bound to the current request user workspace."""
    user_id = resolve_session_user(runtime)
    workspace_dir, _, _ = ensure_workspace_dirs(user_id)
    return FilesystemBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
    )


# 模型初始化：仅在 openai provider 下强制要求 base_url 与 api_key
model_kwargs: dict[str, Any] = {}
if MODEL_PROVIDER == "openai":
    model_kwargs["base_url"] = require_env("MINXIN_OPENAI_BASE_URL")
    require_env("OPENAI_API_KEY")

model = init_chat_model(
    MODEL_NAME,
    model_provider=MODEL_PROVIDER,
    **model_kwargs,
)

# Agent 组装：工具、后端、中间件、提示词
tools: list[Any] = [export_data, upload_file]
backend = create_shell_backend
middleware = [
    MemoryMiddleware(
        backend=create_filesystem_backend,
        sources=["/AGENTS.md"],
    ),
    SkillsMiddleware(
        backend=create_filesystem_backend,
        sources=["/skills"],
    ),
]
system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
    "{current_datetime}",
    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
).replace(
    "{workspace_dir}",
    # "/minxinagents/{user_id}/workspace",
    "/Users/lzzzii/home/hhscc-lz-workspace/minxinagents/services/minxin-analysis-agent/sessions/{user_id}/workspace",
).replace(
    "{skills_dir}",
    # "/minxinagents/{user_id}/workspace/skills",
    "/Users/lzzzii/home/hhscc-lz-workspace/minxinagents/services/minxin-analysis-agent/sessions/{user_id}/workspace/skills",
)

agent = create_deep_agent(
    model=model,
    system_prompt=system_prompt,
    tools=tools,
    backend=backend,
    middleware=middleware,
    interrupt_on={},
    context_schema=Context,
)

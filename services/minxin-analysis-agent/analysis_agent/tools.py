"""Minxin-specific tools implemented locally for backend deployment."""

from __future__ import annotations

from typing import Any


def export_data(query: dict) -> dict[str, Any]:
    """Execute an Elasticsearch DSL query and export the result set to object storage.

    This tool runs the given ES search query against the t_complaints index,
    writes the results to an Excel file, and uploads it to the configured object
    storage (MinIO). The caller receives a pre-signed download URL valid for 12 hours.

    Use this tool when the user asks to export, download, or save query results
    — for example detail records filtered by region, date range, or status.

    Args:
        query: Elasticsearch search body (the dict passed to es.search body parameter).
               Should be a standard ES Query DSL dict, e.g.:
               {"query": {"bool": {"filter": [...]}}, "_source": [...]}

    Returns:
        Dictionary containing:
        - success: Whether the export completed successfully
        - url: Pre-signed download URL for the exported file (valid 12 hours)

    IMPORTANT: After using this tool:
    1. The download URL is automatically displayed to the user by the interface — do NOT repeat or output the URL in your response.
    2. Only tell the user the export succeeded and how many rows were exported.
    3. If the export failed, relay the error message to the user.
    """
    import io
    import os
    import uuid
    from datetime import timedelta

    import pandas as pd
    from elasticsearch import Elasticsearch
    from elasticsearch import helpers as es_helpers
    from minio import Minio

    _MINIO_INTERNAL = "172.17.3.61:8882"
    _MINIO_PUBLIC = "202.97.181.107:8882"
    _BUCKET = "minxinagent"
    _MAX_ROWS = 100_000

    try:
        # 1. 查询 ES，并使用 scan 方式分页拉取后组装为表格数据
        es = Elasticsearch(
            os.environ["ES_HOST"],
            basic_auth=(os.environ["ES_USER"], os.environ["ES_PASS"]),
            ca_certs=os.environ["ES_CA_CERT"],
            request_timeout=60,
        )
        hits = es_helpers.scan(
            es,
            index="t_complaints",
            query=query,
            size=500,
        )
        rows = []
        for hit in hits:
            rows.append(hit["_source"])
            if len(rows) > _MAX_ROWS:
                return {
                    "success": False,
                    "url": "",
                    "error": f"数据量超过导出上限（{_MAX_ROWS:,} 条），请缩小查询范围后重试。",
                }
        df = pd.DataFrame(rows)

        # 2. 空结果校验
        if df.empty:
            return {"success": False, "url": "", "error": "查询结果为空，无数据可导出。"}

        # 3. 在内存中写入 Excel 文件
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)
        file_size = buffer.getbuffer().nbytes

        # 5. 通过内网地址上传到对象存储
        object_name = (
            f"exports/{pd.Timestamp.now().strftime('%Y%m%d')}/"
            f"{uuid.uuid4().hex[:12]}.xlsx"
        )
        client = Minio(
            _MINIO_INTERNAL,
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=False,
        )
        client.put_object(
            _BUCKET,
            object_name,
            buffer,
            length=file_size,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

        # 6. 使用公网地址生成预签名下载链接。
        #    预签名必须基于公网 host 计算，签名后再替换 host 会失效。
        public_client = Minio(
            _MINIO_PUBLIC,
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=False,
        )
        public_url = public_client.presigned_get_object(
            _BUCKET,
            object_name,
            expires=timedelta(hours=12),
        )

        return {"success": True, "url": public_url}

    except Exception as e:  # noqa: BLE001
        return {"success": False, "url": "", "error": str(e)}


def upload_file(file_path: str) -> dict[str, Any]:
    """Upload a workspace file to cloud storage and return a browser-accessible download URL.

    ONLY call this tool when the user explicitly asks to download or save a file
    (e.g. "帮我下载这份报告", "导出一下", "我要这个文件").
    Do NOT call it automatically after writing or generating a file — the user
    may want to continue refining the content before downloading.

    Args:
        file_path: Path to the file. Absolute or relative to the current
                   working directory. Use the same path passed to write_file.

    Returns:
        Dictionary containing:
        - success: Whether the upload succeeded
        - url: Pre-signed download URL valid for 12 hours
        - filename: The original filename

    IMPORTANT: After using this tool:
    1. Tell the user the file is ready and briefly describe its content.
    2. Do NOT repeat the URL in your response — it is displayed automatically.
    3. If upload failed, relay the error message to the user.
    """
    import mimetypes
    import os
    import uuid
    from datetime import datetime, timedelta
    from pathlib import Path

    from minio import Minio
    from analysis_agent.session import (
        ensure_workspace_dirs,
        is_path_inside_workspace,
        resolve_session_user,
    )

    _MINIO_INTERNAL = "172.17.3.61:8882"
    _MINIO_PUBLIC = "202.97.181.107:8882"
    _BUCKET = "minxinagent"

    try:
        session_user = resolve_session_user()
        workspace, _, _ = ensure_workspace_dirs(session_user)

        path = Path(file_path)
        if path.is_absolute():
            resolved_input = path.resolve()
            if is_path_inside_workspace(resolved_input, workspace):
                path = resolved_input
            else:
                # 对绝对路径按“虚拟根路径”语义处理：/a.txt -> {workspace}/a.txt
                path = (workspace / str(path).lstrip("/")).resolve()
        else:
            path = (workspace / path).resolve()

        # 安全限制：仅允许上传用户工作区内文件
        if not is_path_inside_workspace(path, workspace):
            return {
                "success": False,
                "url": "",
                "error": f"只允许上传工作区内的文件。工作区路径：{workspace}",
            }

        if not path.exists():
            return {"success": False, "url": "", "error": f"文件不存在：{path}"}
        if not path.is_file():
            return {"success": False, "url": "", "error": f"路径不是文件：{path}"}

        # 根据扩展名推断 MIME 类型；失败时回退为二进制类型
        content_type, _ = mimetypes.guess_type(str(path))
        content_type = content_type or "application/octet-stream"

        # 文本类型必须携带 charset，避免浏览器按系统默认编码导致乱码。
        if content_type.startswith("text/"):
            content_type = f"{content_type}; charset=utf-8"

        # 对浏览器会内联展示的类型，强制下载，避免直接打开预览。
        _INLINE_TYPES = {"text/plain", "text/markdown", "text/csv", "text/html"}
        base_type = content_type.split(";")[0].strip()
        force_download = base_type in _INLINE_TYPES

        # 对象路径格式：uploads/{user}/{date}/{short-uuid}_{filename}
        date_prefix = datetime.now().strftime("%Y%m%d")
        object_name = (
            f"uploads/{session_user}/{date_prefix}/"
            f"{uuid.uuid4().hex[:12]}_{path.name}"
        )

        # 使用内网地址上传对象
        client = Minio(
            _MINIO_INTERNAL,
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=False,
        )
        client.fput_object(_BUCKET, object_name, str(path), content_type=content_type)

        # 预签名下载链接必须按公网 host 签名；
        # 与 export_data 同理，签名后替换 host 会失效。
        public_client = Minio(
            _MINIO_PUBLIC,
            access_key=os.environ["MINIO_ROOT_USER"],
            secret_key=os.environ["MINIO_ROOT_PASSWORD"],
            secure=False,
        )

        extra_params = {}
        if force_download:
            # 对非 ASCII 文件名进行 URL 编码（例如中文文件名）
            from urllib.parse import quote
            encoded_name = quote(path.name, safe="")
            extra_params["response-content-disposition"] = (
                f"attachment; filename*=UTF-8''{encoded_name}"
            )

        url = public_client.presigned_get_object(
            _BUCKET,
            object_name,
            expires=timedelta(hours=12),
            response_headers=extra_params if extra_params else None,
        )

        return {"success": True, "url": url, "filename": path.name}

    except Exception as e:  # noqa: BLE001
        return {"success": False, "url": "", "error": str(e)}

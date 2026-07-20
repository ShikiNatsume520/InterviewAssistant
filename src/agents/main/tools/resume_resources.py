"""Main Agent只读简历资源工具。"""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import Field

from kernel.resumes import ResumeRepository


def _principal_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {})
    return str(configurable.get("user_id", "default"))


@tool
def list_resumes(config: RunnableConfig) -> str:
    """列出当前用户已经上传的简历元数据，不返回 Markdown 正文。"""
    repository = ResumeRepository()
    try:
        items = repository.list(_principal_id(config))
        return json.dumps(
            {
                "status": "ok" if items else "no_documents",
                "items": [asdict(item) for item in items],
            },
            ensure_ascii=False,
        )
    finally:
        repository.close()


@tool
def search_resumes(
    config: RunnableConfig,
    query: str = Field(description="要在简历正文中精确查找的关键词或短语。"),
    resume_id: str | None = Field(
        default=None,
        description="可选。只搜索这份简历；必须来自前端上下文或简历工具真实返回。",
    ),
) -> str:
    """按关键词搜索当前用户的一份或全部简历，并返回行级命中。"""
    repository = ResumeRepository()
    try:
        result = repository.search(_principal_id(config), query, resume_id)
        return json.dumps(asdict(result), ensure_ascii=False)
    finally:
        repository.close()


@tool
def read_resume(
    config: RunnableConfig,
    resume_id: str = Field(
        description="要读取的简历 ID；必须来自前端上下文或简历工具真实返回。"
    ),
    start_line: int = Field(default=1, ge=1, description="读取起始行，1-based。"),
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="可选结束行；服务端仍会限制单次最大行数和字符数。",
    ),
) -> str:
    """受控读取当前用户指定简历的正文，返回行号和分页信息。"""
    repository = ResumeRepository()
    try:
        result = repository.read(
            _principal_id(config), resume_id, start_line, end_line
        )
        return json.dumps(asdict(result), ensure_ascii=False)
    finally:
        repository.close()


RESUME_RESOURCE_TOOLS = [list_resumes, search_resumes, read_resume]

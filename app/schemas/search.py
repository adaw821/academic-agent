from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    """问题接收接口"""
    model_config = ConfigDict(str_strip_whitespace=True)

    # 用户问题：支持长题设，按字符数限制，不截断内容。
    query: str = Field(min_length=1, max_length=10000)
    #需要检索子片占总体子片的百分比
    candidate_k_percentage: float = Field(
        default=0.4, gt=0, le=1, allow_inf_nan=False,
        description="召回比例，0.4 表示 40%；按当前范围内子片总数向上取整。",
    )
    # 不传或 null 表示整个项目；显式空列表无效，避免意外扩大范围。
    doc_ids: list[Annotated[int, Field(strict=True, gt=0)]] | None = Field(
        default=None,
        min_length=1,
        description="限定检索文档 ID；必须属于 URL 中的项目且已完成处理。省略或 null 表示整个项目。",
    )

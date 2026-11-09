from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchRequest(BaseModel):
    """问题接收接口"""
    model_config = ConfigDict(str_strip_whitespace=True)

    # 用户问题：支持长题设，按字符数限制，不截断内容。
    query: str = Field(min_length=1, max_length=10000)
    allow_school_actions: bool = Field(default=False, description="允许学校课表获取、图片补充、课表增删改、下载附件、导入当前项目和标记提醒已读。")
    timetable_image_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$', description='本轮课表图片附件，由上传接口返回，服务器校验账号归属')
    allow_school_sync: bool = Field(default=False, description="允许同步单门课程；本地名称未匹配时自动发现学校课程并在唯一匹配后首次导入。指定文档或选区时不执行同步。")
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

    #会话id
    conversation_id: int | None = Field(default=None, ge=1)
    # 划线问答时一起传入；普通问答省略这两个字段。
    document_id: int | None = Field(default=None, ge=1)
    selected_text: str | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_selection(self) -> "SearchRequest":
        if self.timetable_image_id and (self.doc_ids or self.document_id):
            raise ValueError('课表图片不能同时限定文档或选区')
        if (self.document_id is None) != (self.selected_text is None):
            raise ValueError("document_id 和 selected_text 必须一起提供")
        if self.document_id is not None and self.doc_ids is not None and self.document_id not in self.doc_ids:
            raise ValueError("划线文档 document_id 必须包含在 doc_ids 检索范围中")
        return self

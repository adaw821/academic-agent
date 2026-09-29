from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchRequest(BaseModel):
    """问题接收接口"""
    model_config = ConfigDict(str_strip_whitespace=True)

    # 用户问题：支持长题设，按字符数限制，不截断内容。
    query: str = Field(min_length=1, max_length=10000)
    #需要检索几个子片
    candidate_k: int = Field(default=50, ge=1, le=100)
    #会话id
    conversation_id: int | None = Field(default=None, ge=1)
    # 划线问答时一起传入；普通问答省略这两个字段。
    document_id: int | None = Field(default=None, ge=1)
    selected_text: str | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_selection(self) -> "SearchRequest":
        if (self.document_id is None) != (self.selected_text is None):
            raise ValueError("document_id 和 selected_text 必须一起提供")
        return self

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    """问题接收接口"""
    model_config = ConfigDict(str_strip_whitespace=True)

    # 用户问题：支持长题设，按字符数限制，不截断内容。
    query: str = Field(min_length=1, max_length=10000)
    #需要检索几个子片
    candidate_k: int = Field(default=50, ge=1, le=100)

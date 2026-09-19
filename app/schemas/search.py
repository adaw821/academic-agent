from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    """问题接收接口"""
    model_config = ConfigDict(str_strip_whitespace=True)

    #用户问题
    query: str = Field(min_length=1, max_length=2000)
    #最多检索几个子片
    top_k: int = Field(default=5, ge=1, le=20)
from pydantic import BaseModel, Field

class ProjectCreate(BaseModel):
    # 【前端配套修改】原来 user_id 必填，现在可省略，前端只需提交项目名称。
    # 保留此字段兼容旧请求；路由始终用登录用户的 ID，忽略这里传入的值。
    user_id: int | None = None
    name: str = Field(min_length=1, max_length=100)

from pydantic import BaseModel, model_validator
from pydantic import BaseModel, Field, model_validator


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_contact(self):
        if self.email:
            self.email = self.email.strip()
        if self.phone:
            self.phone = self.phone.strip()

        if not self.email and not self.phone:
            raise ValueError("必须填写邮箱或手机号")

        # 空字符串统一变成 None，避免存进数据库
        self.email = self.email or None
        self.phone = self.phone or None
        return self

class UserLogin(BaseModel):
    account: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_account(self):
        self.account = self.account.strip()

        if not self.account:
            raise ValueError("请输入邮箱或手机号")

        return self

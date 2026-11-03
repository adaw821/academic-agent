from typing import Literal
import re
from pydantic import BaseModel, Field, field_validator, model_validator


class EmailInput(BaseModel):
    email: str = Field(max_length=255)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
            raise ValueError('请输入有效邮箱地址')
        return value


class EmailCodeRequest(EmailInput):
    purpose: Literal['register', 'reset']


class PasswordReset(EmailInput):
    code: str = Field(pattern=r'^[0-9]{6}$')
    password: str = Field(min_length=8, max_length=128)


class UserCreate(PasswordReset):
    username: str = Field(min_length=1, max_length=20)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator('phone')
    @classmethod
    def valid_phone(cls, value):
        value = value.strip() if value else None
        if not value:
            return None
        if not re.fullmatch(r'\+?[0-9]{7,15}', value):
            raise ValueError('手机号需为 7–15 位数字，可在开头加 + 国家区号')
        return value


class UserLogin(BaseModel):
    account: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_account(self):
        self.account = self.account.strip()

        if not self.account:
            raise ValueError("请输入邮箱或手机号")

        return self

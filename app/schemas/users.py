from typing import Literal
import re
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from fastapi import HTTPException


class SchoolCredentials(BaseModel):
    school_account: str
    school_password: SecretStr

    @model_validator(mode='before')
    @classmethod
    def valid_credentials(cls, data):
        # HTTPException avoids FastAPI echoing raw password input in validation errors.
        if not isinstance(data, dict):
            raise HTTPException(422, '请输入学生账号和学校密码')
        if any(field.is_required() and key not in data for key, field in cls.model_fields.items()):
            raise HTTPException(422, '请填写完整的注册或学校连接信息')
        account, password = data.get('school_account'), data.get('school_password')
        if not isinstance(account, str) or not account.strip() or len(account) > 128:
            raise HTTPException(422, '学生账号格式无效')
        if not isinstance(password, str) or not 1 <= len(password) <= 256:
            raise HTTPException(422, '学校密码格式无效')
        return dict(data, school_account=account.strip())


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


class UserCreate(PasswordReset, SchoolCredentials):
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

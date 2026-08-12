from pydantic import BaseModel, model_validator
from pwdlib import PasswordHash
from users import User
from fastapi import Depends
from app.db.database import get_db

class UserCreate(BaseModel):
    username:str
    password:str
    email:str|None = None
    phone:str|None = None

    def check_duplicate(db:Session, email: str, phone:str):
        if email:
            user = db.scalar(
                select(User).where(User.email == email)
            )
            if user:
                raise ValueError("邮箱已经注册")

        if phone:
            user = db.scalar(
                select(User).where(User.phone == phone)
            )
            if user:
                raise ValueError("手机号已经注册")



    @model_validator(mode="after")
    def validate_contact(self):
        email = self.email
        phone = self.phone
        if email or phone:
            return self
        raise ValueError("必须要填电话或邮件")
    
    def hash_password(password: str) -> str:
        password_hash = PasswordHash.recommended()
        hashed = password_hash.hash(password)
        return hashed
    
class UserLogin(BaseModel):
    username:str
    password:str

    def check_identity(db:Session):
        username = self.username
        password = self.password
        user = db.scalar(
                select(User).where(User.username == username)
            )
        if not user:
            raise ValueError("请先登录")
        
        

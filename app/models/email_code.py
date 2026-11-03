from sqlalchemy import Column, String, Integer, BigInteger
from app.db.database import Base


class EmailCode(Base):
    __tablename__ = 'email_codes'
    email = Column(String(255), primary_key=True)
    purpose = Column(String(16), primary_key=True)
    digest = Column(String(64), nullable=False)
    expires = Column(BigInteger, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)


class EmailRate(Base):
    __tablename__ = 'email_rates'
    key = Column(String(64), primary_key=True)
    until = Column(BigInteger, nullable=False)
    count = Column(Integer, nullable=False)

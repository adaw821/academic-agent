from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from sqlalchemy import CheckConstraint

class User(Base):
    __tablename__ = "users"

    # Versioned AES-GCM envelope; never returned by user APIs.
    school_credentials: Mapped[str | None] = mapped_column(String)
    school_sync_state: Mapped[str | None] = mapped_column(String(30))
    school_sync_message: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL",
            name="email_and_phone_check",
            ),
    )
    
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    username: Mapped[str]=mapped_column(
        String(20),
        nullable=False
    )

    email: Mapped[str|None]=mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )

    phone: Mapped[str|None]=mapped_column(
        String(20),
        nullable=True,
        unique=True
    )

    courses: Mapped[list["Course"]]=relationship(
        back_populates="user"
    )

    password_hash: Mapped[str]=mapped_column(
        String(255),
        nullable=False
    )

    projects: Mapped[list["Project"]]=relationship(
        back_populates="user"
    )

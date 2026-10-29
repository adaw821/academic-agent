"""One inbox reminder per assignment deadline; reads do not generate reminders."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class DeadlineReminder(Base):
    __tablename__ = 'deadline_reminders'
    __table_args__ = (UniqueConstraint('assignment_id', 'due_at', name='uq_deadline_reminder_assignment_due'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

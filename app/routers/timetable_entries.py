"""HTTP access to the same timetable operations used by the agent."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.users import User
from app.services.user_services import get_currentUser
from app.services import timetable_entries as entries
from app.services.timetable_entries import ScheduleFields, NewSchedule

router = APIRouter(tags=['timetable'])

@router.post('/timetable/entries', status_code=201)
def create_entry(request: NewSchedule, db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    return entries.create_entry(request, db, user.id)

@router.put('/timetable/entries/{schedule_id}')
def update_entry(schedule_id: int, request: ScheduleFields, db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    return entries.update_entry(schedule_id, request, db, user.id)

@router.delete('/timetable/entries/{schedule_id}')
def delete_entry(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    return entries.delete_entry(schedule_id, db, user.id)

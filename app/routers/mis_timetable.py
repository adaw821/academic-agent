import base64
from fastapi import APIRouter, Depends, HTTPException, Response
from app.models.users import User
from app.services.user_services import get_currentUser
from app.routers.school import require_local_login
from app.services.mis_timetable import mis_capture, read_snapshot, enabled

router = APIRouter(tags=['MIS timetable'])


@router.get('/timetable/mis')
def saved_timetable(user: User = Depends(get_currentUser)):
    snapshot = read_snapshot(user.id)
    return {'local_login': enabled() and not bool(user.school_credentials), 'managed': bool(user.school_credentials),
            'snapshot': dict({k: v for k, v in snapshot.items() if k != 'image'}, has_image=bool(snapshot.get('image'))) if snapshot else None}


@router.get('/timetable/mis/image')
def saved_image(user: User = Depends(get_currentUser)):
    snapshot = read_snapshot(user.id)
    if not snapshot or not snapshot.get('image'):
        raise HTTPException(404, '尚未保存 MIS 课表')
    return Response(base64.b64decode(snapshot['image']), media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.get('/timetable/mis/capture')
def capture_status(user: User = Depends(get_currentUser)):
    return mis_capture.status(user.id)


@router.post('/timetable/mis/capture', dependencies=[Depends(require_local_login)])
def start_capture(user: User = Depends(get_currentUser)):
    try:
        return mis_capture.start(user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.put('/timetable/mis/capture', dependencies=[Depends(require_local_login)])
def save_capture(user: User = Depends(get_currentUser)):
    try:
        return mis_capture.request_capture(user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete('/timetable/mis/capture', dependencies=[Depends(require_local_login)])
def cancel_capture(user: User = Depends(get_currentUser)):
    return mis_capture.cancel(user.id)

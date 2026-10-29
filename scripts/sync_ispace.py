"""Local iSpace collector: preview by default; persist only with --sync --user-id."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ispace import ISpaceClient, SchoolError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-file', type=Path, required=True, help='Local JSON containing MoodleSession; never commit this file')
    parser.add_argument('--course-url')
    parser.add_argument('--list-courses', action='store_true')
    parser.add_argument('--sync', action='store_true')
    parser.add_argument('--user-id', type=int, help='Local application user, not school user ID')
    args = parser.parse_args()
    if bool(args.course_url) == args.list_courses:
        parser.error('Specify exactly one of --course-url or --list-courses')
    if args.sync and (args.user_id is None or args.list_courses):
        parser.error('--sync requires --course-url and --user-id')
    try:
        credentials = json.loads(args.session_file.read_text(encoding='utf-8-sig'))
        cookie = credentials.get('MoodleSession')
        if not isinstance(cookie, str):
            raise SchoolError('本地会话文件必须包含 MoodleSession 字符串')
        with ISpaceClient(cookie) as client:
            if args.list_courses:
                output = client.discover_courses()
            else:
                snapshot = client.collect_course(args.course_url)
                output = snapshot.to_dict()
                if args.sync:
                    from app.db.database import SessionLocal
                    from app.services.school_sync import sync_course
                    with SessionLocal() as db:
                        output = sync_course(db, args.user_id, snapshot)
        print(json.dumps(output, ensure_ascii=False, indent=2, default=lambda v: v.isoformat()))
    except (SchoolError, OSError, ValueError) as exc:
        # Never print cookies, response headers, SQL parameters or credential file contents.
        print(f'采集失败：{exc}' if isinstance(exc, SchoolError) else '本地配置或输入无效，请检查会话文件和用户 ID。', file=sys.stderr)
        return 1
    except Exception:
        print('同步失败，数据已回滚；请检查数据库迁移和连接配置。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

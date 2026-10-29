"""Periodically refresh already imported courses for one local application user."""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=int, required=True, help='Local application user ID, not school ID')
    parser.add_argument('--interval-minutes', type=int, default=60, help='5..10080; default 60; wait after each batch')
    parser.add_argument('--once', action='store_true', help='Run one due-course batch and exit')
    parser.add_argument('--deadline-reminders', action='store_true', help='Generate inbox reminders for pending deadlines within 24 hours')
    args = parser.parse_args(argv)
    if args.user_id <= 0 or not 5 <= args.interval_minutes <= 10080:
        parser.error('Require a positive --user-id and --interval-minutes between 5 and 10080')
    stop = threading.Event()
    previous = {}
    try:
        # Lazy import keeps --help usable without database credentials or model setup.
        from app.services.school_scheduler import run_schedule, SyncAlreadyRunning
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            return run_schedule(args.user_id, args.interval_minutes * 60, once=args.once, stop=stop,
                                deadline_reminders=args.deadline_reminders,
                                emit=lambda report: print(json.dumps(report, ensure_ascii=False), flush=True))
        except SyncAlreadyRunning:
            print('该账号已有本地同步进程，请勿重复启动。', file=sys.stderr)
            return 2
    except Exception:
        print('定时同步启动或运行失败，请检查数据库配置、迁移和本地目录权限。', file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())

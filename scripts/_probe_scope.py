"""先搞清楚数据在哪：列出每个项目的文档/子片数，以及文档 status 的实际取值。
检索只认 status=='completed'，这里看看你的库里到底是不是这个值、哪个项目有数据。
    python -m scripts._probe_scope
"""

from app.config import load_config

load_config()

from app.db.database import SessionLocal
# 同样靠 import 注册全部模型
from app.models.users import User
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.course_timetable import CourseSchedule
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child

from sqlalchemy import select, func


def main():
    db = SessionLocal()
    try:
        print("=== 文档 status 的所有取值（检索只认 'completed'）===")
        for status, cnt in db.execute(
            select(Document.status, func.count(Document.id)).group_by(Document.status)
        ).all():
            print(f"    status={status!r:20} 文档数={cnt}")

        print("\n=== 每个项目：文档总数 / 已完成文档 / 已完成子片数 ===")
        rows = db.execute(
            select(Project.id, Project.name, func.count(func.distinct(Document.id)))
            .outerjoin(Document, Document.project_id == Project.id)
            .group_by(Project.id, Project.name)
            .order_by(Project.id)
        ).all()
        for pid, name, doc_total in rows:
            done = db.scalar(
                select(func.count(Document.id))
                .where(Document.project_id == pid, Document.status == "completed")
            )
            children = db.scalar(
                select(func.count(Child.id))
                .join(Parent, Parent.id == Child.parent_id)
                .join(Document, Document.id == Parent.document_id)
                .where(Document.project_id == pid, Document.status == "completed")
            )
            print(f"    project id={pid:<4} name={name!r:30} 文档={doc_total:<4} "
                  f"已完成={done:<4} 已完成子片={children}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

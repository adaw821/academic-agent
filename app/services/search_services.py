from app.services.chunk import get_embedding_model

from sqlalchemy.orm import Session
from app.models.document_child import Child
from sqlalchemy import select
from app.models.document_parents import Parent
from app.models.documents import Document

def embedding_query(query):
    """将问题转换成向量"""
    model =  get_embedding_model()

    vector = model.encode(
    query,
    normalize_embeddings=True
    )
    if vector is None:
        raise ValueError("问题向量化失败")

    return vector.tolist()

def search_children(
        query: str,
        project_id: int,
        db: Session,
        top_k: int
):
    """用来查找问题相似文件的"""
    query_vector = embedding_query(query)
    distance = Child.embedding.cosine_distance(query_vector)

    #查询距离最近的五个子片，以及它们对应的父片，同时按照project_id和文件状态过滤不需要的
    stmt = (
        select(Child, Parent, Document, distance.label("distance"))
        .join(Parent, Parent.id == Child.parent_id)
        .join(Document, Document.id == Parent.document_id)
        .where(
            Document.project_id == project_id,
            Document.status == "completed"
        )
        .order_by(distance.asc(), Child.id.asc())
        .limit(top_k)
    )

    #查出来的结果,里边是child对象和distance
    rows = db.execute(stmt).all()
    
    #整理查到的子片
    results = []
    #用来记录召回的父片id,用来去重
    seen_parent_ids = set()

    for child, parent, document, distance_value in rows:
        if parent.id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent.id)
        results.append({
            "child_id": child.id,
            "parent_id": parent.id,
            "child_content": child.content,
            "parent_content": parent.content,
            "document_id": document.id,
            "filename": document.filename,
            "distance": float(distance_value),
        })

    return results

    
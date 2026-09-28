from app.services.chunk import get_embedding_model

from sqlalchemy.orm import Session
from app.models.document_child import Child
from sqlalchemy import select, func
from app.models.document_parents import Parent
from app.models.documents import Document

import re
from decimal import Decimal
from math import ceil
import jieba
from rank_bm25 import BM25Okapi
from functools import lru_cache
from sentence_transformers import CrossEncoder
from torch.nn import Sigmoid


def tokenize_for_bm25(text: str) -> list[str]:
    """把一句话拆成词"""
    #统一大小写
    text = text.casefold()


    return [
        token
        #把文本拆成词
        for word in jieba.cut_for_search(text)
        #去空白检查是不是有效内容，有效才返回
        if (token := word.strip()) and re.search(r"\w", token)
    ] 

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

def percentage_candidate_count(total_chunks: int, percentage: float) -> int:
    """按 0～1 比例向上取整；避免浮点误差导致整除时多取一条。"""
    return ceil(total_chunks * Decimal(str(percentage)))


#向量召回
def search_vector(
        query: str,
        project_id: int,
        db: Session,
        candidate_k_percentage: float,
        doc_ids: list[int] | None = None,
):
    """按比例召回当前范围内距离最近的子片，0.4 表示 40%。"""
    filters = [
        Document.project_id == project_id,
        Document.status == "completed",
    ]
    if doc_ids is not None:
        filters.append(Document.id.in_(doc_ids))

    # 分母只包含当前项目/指定文档中处理完成的子片。
    count_stmt = (
        select(func.count(Child.id))
        .select_from(Child)
        .join(Parent, Parent.id == Child.parent_id)
        .join(Document, Document.id == Parent.document_id)
        .where(*filters)
    )
    total_chunks = db.scalar(count_stmt) or 0
    if total_chunks == 0:
        return []
    candidate_limit = percentage_candidate_count(total_chunks, candidate_k_percentage)

    query_vector = embedding_query(query)
    distance = Child.embedding.cosine_distance(query_vector)



    #查询距离最近的子片，以及它们对应的父片，同时按照project_id和文件状态过滤不需要的
    stmt = (
        select(Child, Parent, Document, distance.label("distance"))
        .join(Parent, Parent.id == Child.parent_id)
        .join(Document, Document.id == Parent.document_id)
        .where(*filters)
        .order_by(distance.asc(), Child.id.asc())
        .limit(candidate_limit)
    )

    #查出来的结果,里边是child对象和distance
    rows = db.execute(stmt).all()
    
    #整理查到的子片
    results = []

    for child, parent, document, distance_value in rows:
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

#关键词召回
def search_bm25(
    query: str,
    project_id: int,
    db: Session,
    candidate_k_percentage: float,
    doc_ids: list[int] | None = None,
):
    query_tokens = tokenize_for_bm25(query)
    if not query_tokens:
        return []

    #从当前项目查出相关子片
    stmt = (
    select(
        Child.id.label("child_id"),
        Child.parent_id.label("parent_id"),
        Child.content.label("child_content"),
        Parent.content.label("parent_content"),
        Document.id.label("document_id"),
        Document.filename.label("filename"),
    )
    .join(Parent, Parent.id == Child.parent_id)
    .join(Document, Document.id == Parent.document_id)
    .where(
        Document.project_id == project_id,
        Document.status == "completed",
    )
    .order_by(Child.id.asc())
)

    if doc_ids is not None:
        stmt = stmt.where(Document.id.in_(doc_ids))

    rows = db.execute(stmt).mappings().all()
    # 和向量召回使用相同分母，不按关键词命中的数量计算百分比。
    candidate_limit = percentage_candidate_count(len(rows), candidate_k_percentage)

    records = []
    child_contents_token = []
    #把找到的所有子片再切一次
    for row in rows:
        token = tokenize_for_bm25(row["child_content"])
        if not token:
            continue
        #收集切好的子片
        child_contents_token.append(token)
        #收集每一行数据 方便返回
        records.append(dict(row))

    if not records:
        return []
    
    bm25 = BM25Okapi(child_contents_token)

    scores = bm25.get_scores(query_tokens)

    query_terms = set(query_tokens)
    #找出来能跟问题对的上的子片, 并得到分数
    matched_items=[]
    for index, tokens in enumerate(child_contents_token):
        if query_terms.intersection(tokens):
            matched_items.append({
                "index": index,
                "score": float(scores[index]),
            })
    #对score降序排序
    matched_items.sort(
    key=lambda item: item["score"],
    reverse=True,
)

    results = []
    for matched_item in matched_items[:candidate_limit]:
        record = records[matched_item["index"]].copy()
        record["bm25_score"] = matched_item["score"]
        results.append(record)

    return results

#做rrf排序
def rrf_fuse(
        vector_results: list[dict],
        BM25_results: list[dict]
):
    merged=[]
    for result in [vector_results, BM25_results]:
        for rank, item in enumerate(result, start=1):
            ranking_score = 1/(rank+60)
            child_id = item["child_id"]

            found = False
            for merge in merged:
                if merge["child_id"]==child_id:
                    merge["ranking_score"]+=ranking_score
                    found=True
                    break

            if not found:
                childs=item.copy()
                childs["ranking_score"]=ranking_score
                merged.append(childs)

            merged.sort(
                key=lambda item:item["ranking_score"],
                reverse=True
            )

    return merged

def rerank_results(
    original_query: str,
    translated_query: str | None,
    merged_results: list[dict],
) -> list[dict]:
    """对子片做双查询评分，再取各父片的最高子片分数并返回父片正文。"""
    if not merged_results:
        return []

    # 同一子片只评分一次；同一父片下的不同子片都要参与评分。
    children = []
    seen_child_ids = set()

    for item in merged_results:
        child_id = item["child_id"]

        if child_id in seen_child_ids:
            continue

        seen_child_ids.add(child_id)
        children.append(item.copy())

    # 两种查询针对同一组、同一顺序的子片正文评分。
    original_pairs = []
    for item in children:
        original_pairs.append([
            original_query,
            item["child_content"],
        ])

    model = get_reranker()

    original_scores = model.predict(
        original_pairs,
        batch_size=4,
        activation_fn=Sigmoid(),
        )

    translated_scores = None
    if translated_query:
        translated_pairs = [
            [translated_query, item["child_content"]] for item in children
        ]
        translated_scores = model.predict(
            translated_pairs,
            batch_size=4,
            activation_fn=Sigmoid(),
        )

    if len(original_scores) != len(children):
        raise RuntimeError("原问题重排分数数量与子片数量不一致")
    if translated_scores is not None and len(translated_scores) != len(children):
        raise RuntimeError("译文重排分数数量与子片数量不一致")

    best_by_parent = {}
    for index, item in enumerate(children, start=0):
        original_rerank_score = float(original_scores[index])
        item["original_rerank_score"]=original_rerank_score
        if translated_scores is not None:
            translated_rerank_score = float(translated_scores[index])
        else:
            translated_rerank_score = None
        item["translated_rerank_score"] = translated_rerank_score

        item["total_score"] = (
            (original_rerank_score + translated_rerank_score) / 2
            if translated_rerank_score is not None
            else original_rerank_score
        )
        # 先合并同一子片的两种评分，再取父片最高分，不能跨子片拼分。
        # 保留得分最高子片的 ID、原始评分和完整父片正文，便于追溯。
        parent_id = item["parent_id"]
        previous = best_by_parent.get(parent_id)
        if previous is None or item["total_score"] > previous["total_score"]:
            best_by_parent[parent_id] = item

    parents = sorted(
        best_by_parent.values(), key=lambda item: item["total_score"], reverse=True
    )
    
    return chunk_filter(parents)

#过滤
def chunk_filter(
    parents: list[dict],
    min_score: float=0.7,
    top_k:int=5,
    ):
    results = []
    for parent in parents:
        if parent["total_score"]>min_score:
            results.append(parent)
        if len(results)==top_k:
            break

    return results   





@lru_cache(maxsize=1)
def get_reranker():
    return CrossEncoder("BAAI/bge-reranker-v2-m3")

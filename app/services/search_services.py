from app.services.chunk import get_embedding_model

from sqlalchemy.orm import Session
from app.models.document_child import Child
from sqlalchemy import select
from app.models.document_parents import Parent
from app.models.documents import Document
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.answer_services import llm
from app.services.context_management import count_message_tokens, is_over_budget
from fastapi import HTTPException
from uuid import uuid4

import re
import jieba
from rank_bm25 import BM25Okapi
from functools import lru_cache
from sentence_transformers import CrossEncoder
from torch.nn import Sigmoid

from langchain_core.messages import SystemMessage, HumanMessage

REWRITE_CONTEXT_LIMIT = 65_536
REWRITE_OUTPUT_TOKENS = 8_192
REWRITE_SAFETY_MARGIN = 1_024

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

def rewrite_query(
    query: str,
    histories: list[dict],
    summary: str = "",
) -> str:
    """根据历史记录补充问题，方便查找资料完善"""
    if not histories and not summary:
        return query

    history_strings = []
    for history in histories:
        history_string = (
            f"role:{history["role"]}\n\n"
            f"\ncontent:{history["content"]}"
            )
        history_strings.append(history_string)

    history_context = "\n\n".join(history_strings)
    if summary:
        history_context = f"已有历史摘要:\n{summary}\n\n后续历史:\n{history_context}"

    messages = [
        SystemMessage(
        content=(
            "你是一个检索问题改写助手。"
            "请根据历史对话，将当前用户请求改写为无需历史也能理解的独立请求。"
            "只补全历史中有明确依据的指代和省略，保留当前请求的原意、条件和语言。"
            "不要添加无关历史信息，不要编造细节；无法确定的指代不要猜测。"
            "如果当前请求已经完整，原样返回。"
            "只输出改写后的请求，不回答请求，不解释改写过程。"
        )
    ),
        HumanMessage(
            content=(
                f"问题:\n{query}\n\n"
                f"历史资料:\n{history_context}"
            )
        )
    ]

    message_data = [
        {"role": "system", "content": messages[0].content},
        {"role": "user", "content": messages[1].content},
    ]
    if is_over_budget(
        count_message_tokens(message_data),
        REWRITE_CONTEXT_LIMIT, REWRITE_OUTPUT_TOKENS, REWRITE_SAFETY_MARGIN,
    ):
        raise HTTPException(status_code=400, detail="问题和历史超过问题改写的输入预算，请减少输入。")

    response = llm.bind(max_tokens=REWRITE_OUTPUT_TOKENS).invoke(messages)
    if response.response_metadata.get("finish_reason") == "length":
        raise HTTPException(status_code=502, detail="问题改写达到输出上限，未继续检索。")
    if not isinstance(response.content, str):
        raise ValueError("问题改写模型未返回文本")
    return response.content.strip() or query



#向量召回
def search_vector(
        query: str,
        project_id: int,
        db: Session,
        candidate_k: int,
):
    """用来查找问题相似文件的"""
    query_vector = embedding_query(query)
    distance = Child.embedding.cosine_distance(query_vector)

    #查询距离最近的子片，以及它们对应的父片，同时按照project_id和文件状态过滤不需要的
    stmt = (
        select(Child, Parent, Document, distance.label("distance"))
        .join(Parent, Parent.id == Child.parent_id)
        .join(Document, Document.id == Parent.document_id)
        .where(
            Document.project_id == project_id,
            Document.status == "completed"
        )
        .order_by(distance.asc(), Child.id.asc())
        .limit(candidate_k)
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
    candidate_k: int,
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

    rows = db.execute(stmt).mappings().all()

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
    for matched_item in matched_items[:candidate_k]:
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
    query: str,
    merged_results: list[dict],
) -> list[dict]:
    if not merged_results:
        return []

    #先去重
    parents = []
    seen_parent_ids = set()

    for item in merged_results:
        parent_id = item["parent_id"]

        if parent_id in seen_parent_ids:
            continue

        seen_parent_ids.add(parent_id)
        parents.append(item.copy())

    #将问题和每个父片配对,给模型打分再重排
    pairs = []
    for item in parents:
        pairs.append([
            query,
            item["parent_content"],
        ])

    model = get_reranker()
    scores = model.predict(
        pairs,
        batch_size=4,
        activation_fn=Sigmoid(),
        )
    if len(scores) != len(parents):
        raise RuntimeError("重排分数数量与父片数量不一致")

    for index, item in enumerate(parents, start=0):
        rerank_score = float(scores[index])
        item["rerank_score"]=rerank_score

    parents.sort(
    key=lambda item:item["rerank_score"],
    reverse=True
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
        if parent["rerank_score"]>min_score:
            results.append(parent)
        if len(results)==top_k:
            break

    return results

def history_messages_control(
    answer:str,
    query: str,
    conversation_id: int | None,
    db: Session,
    project_id: int
):

    #用来标识某个问题和回答是同一轮
    turn_id = uuid4().hex
    if not conversation_id:
        conversation = Conversation(project_id = project_id)
        db.add(conversation)
        db.flush()

        question = Message(
            conversation_id = conversation.id,
            content = query,
            role = "user",
            turn_id = turn_id
        )

        reply = Message(
            conversation_id = conversation.id,
            content = answer,
            role = "assistant",
            turn_id = turn_id
        )

        db.add_all([question, reply])
        db.commit()
        return conversation.id

    else:
        conversation = db.get(Conversation, conversation_id)
        if not conversation or conversation.project_id != project_id:
            raise HTTPException(status_code=404, detail="会话不存在")

        question = Message(
            conversation_id = conversation_id,
            content = query,
            role = "user",
            turn_id = turn_id
        )

        reply = Message(
            conversation_id = conversation_id,
            content = answer,
            role = "assistant",
            turn_id = turn_id
        )

        db.add_all([question, reply])
        db.commit()
        return conversation_id


@lru_cache(maxsize=1)
def get_reranker():
    return CrossEncoder("BAAI/bge-reranker-v2-m3")

"""Document-level coverage for overview questions, independent of similarity scores."""
import re
from pathlib import Path

from sqlalchemy import select

from app.models.documents import Document
from app.models.document_parents import Parent


def _mentions(query, filename):
    stem = Path(filename).stem
    name_pattern = r"[\s_.-]*".join(re.escape(part) for part in re.split(r"[\s_.-]+", stem))
    if len(stem) >= 2 and re.search(
        rf"(?<![a-z0-9]){name_pattern}(?![a-z0-9])", query, re.I,
    ):
        return True
    # Match course resource identifiers, but never match Lab02 inside Lab020.
    identifier = re.match(r"(?i)(lab|lecture|lesson|chapter|week)[\s_-]*0*(\d+)(?!\d)", stem)
    if identifier:
        return bool(re.search(
            rf"(?i)(?<![a-z0-9]){identifier[1]}[\s_-]*0*{int(identifier[2])}(?!\d)", query
        ))
    return False


def plan_document_overview(query, project_id, db, doc_ids=None, *, allow_implicit=True):
    """Conservative intent detection; resolve names only inside the authorized scope."""
    if not re.search(
        r"主要(?:讲|说|介绍|涉及|包含|内容)|讲了?(?:什么|哪些)|知识点|"
        r"总结|概述|概括|梳理|summari[sz]e|summary|overview|what\s+.*(?:about|cover)",
        query, re.I,
    ):
        return None
    # Local explanations/selected sections still belong to ordinary retrieval.
    if re.search(r"第\s*[\d一二三四五六七八九十]+\s*[页章节]|用法|区别|为什么|如何|怎么|"
                 r"中的|里的|关于|section\s+\d|page\s+\d|\bhow\b", query, re.I):
        return None
    stmt = select(Document).where(Document.project_id == project_id)
    if doc_ids is not None:
        stmt = stmt.where(Document.id.in_(doc_ids))
    documents = db.scalars(stmt.order_by(Document.id)).all()
    matches = [doc for doc in documents if _mentions(query, doc.filename)]
    # Avoid converting a topic summary (e.g. 总结 SQL) into a whole-file overview.
    generic = re.search(r"这(?:份|个|些|篇)|文档|课件|全文|文件|ppt|pdf|document|slides?|file", query, re.I)
    bare = re.fullmatch(
        r"\s*(?:请|帮我|请帮我)?(?:(?:总结|概述|概括|梳理)(?:一下)?|"
        r"(?:主要)?讲了?什么|有哪些知识点|主要内容是什么)[。！？?!.\s]*", query
    )
    named = re.search(
        r"(?i)(?<![a-z0-9])(?:lab|lecture|lesson|chapter|week)[\s_-]*\d+|"
        r"《.+?》|\S+\.(?:pptx|pdf|docx|txt)\b", query
    )
    if not (matches or generic or named or bare):
        return None
    if matches:
        if len(matches) != 1:
            return {"clarification": "找到多份匹配的文档，请在资料栏选择要总结的一份后再提问。"}
        selected = matches
    elif named:
        return {"clarification": "在当前文档范围内未找到对应文件，请在资料栏选择要总结的文档。"}
    elif doc_ids is not None:
        selected = documents
    elif not allow_implicit:
        # With history, "this document" may refer to an earlier target.
        return None
    elif len(documents) == 1:
        selected = documents
    else:
        return {"clarification": "请在资料栏选择要总结的文档，再提出概述问题。"}
    if any(doc.status != "completed" for doc in selected):
        return {"clarification": "目标文档尚未完成处理，请等待处理完成后再总结。"}
    return {"document_ids": [doc.id for doc in selected]}


def read_document_overview(project_id, document_ids, db):
    """Read every indexed parent in document order, preserving citation metadata."""
    rows = db.execute(
        select(Parent, Document).join(Document, Parent.document_id == Document.id)
        .where(Document.project_id == project_id, Document.status == "completed",
               Document.id.in_(document_ids))
        .order_by(Document.id, Parent.chunk_index, Parent.id)
    ).all()
    return [{
        "parent_id": parent.id, "document_id": document.id, "filename": document.filename,
        "parent_content": parent.content, "section_id": parent.section_id,
        "heading_path": parent.heading_path, "page_numbers": parent.page_numbers,
    } for parent, document in rows if parent.content.strip()]

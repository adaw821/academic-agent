"""Document-level coverage for overview questions, independent of similarity scores."""
import re
from pathlib import Path

from sqlalchemy import select

from app.models.documents import Document
from app.models.document_parents import Parent


# 规范类型 → 英文别名(大小写不敏感);中文另处理。
_TYPE_ALIASES = {
    "lecture": ["lecture", "lec"],
    "lab": ["lab"],
    "chapter": ["chapter", "ch"],
    "week": ["week", "wk"],
    "tutorial": ["tutorial", "tut"],
    "homework": ["homework", "hw"],
    "assignment": ["assignment", "assign", "asgn"],
    "lesson": ["lesson"],
    "quiz": ["quiz"],
    "project": ["project", "proj"],
}
_CN_TYPE = {"讲": "lecture", "课": "lecture", "章": "chapter", "周": "week"}
_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九", start=0)}


def _cn_to_int(token):
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token in _CN_DIGIT:
        return _CN_DIGIT[token]
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _identifier_keys(text):
    """从文本里抽出所有 (规范类型, 编号) key;前导 0 无关。"""
    keys = set()
    low = text.casefold()
    for canon, aliases in _TYPE_ALIASES.items():
        for alias in aliases:
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}[\s_\-]*0*(\d+)(?![0-9])", low):
                keys.add((canon, int(m.group(1))))
    for m in re.finditer(r"第\s*([0-9一二三四五六七八九十]+)\s*([讲课章周])", text):
        n = _cn_to_int(m.group(1))
        if n is not None:
            keys.add((_CN_TYPE[m.group(2)], n))
    for m in re.finditer(r"作业\s*0*(\d+)", text):
        keys.add(("homework", int(m.group(1))))
    for m in re.finditer(r"实验\s*0*(\d+)", text):
        keys.add(("lab", int(m.group(1))))
    return keys


def _full_name_hit(query, stem):
    """整段文件名(灵活分隔)出现在问题里。"""
    if len(stem) < 2:
        return False
    pattern = r"[\s_.\-]*".join(re.escape(p) for p in re.split(r"[\s_.\-]+", stem) if p)
    return bool(pattern) and bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", query, re.I))


# 课程框定词:出现才启用"标题短语"匹配,避免把纯概念词当文件名。
_FRAMING = re.compile(r"课件|这\s*(?:门课|节课?|一?讲|篇|份|个?文件|个?文档|课)")
# 太通用的标题部分不单独触发(没编号/框定词时歧义太大)。
_GENERIC_TITLE = {"intro", "introduction", "math", "notes", "note", "review",
                  "overview", "summary", "slides", "slide", "概论", "简介", "导论"}
# 剥标题前缀的类型词(含 course/unit 等);要求其后跟编号或分隔符,避免误剥 Chemistry→emistry。
_TITLE_TYPES = sorted(
    {a for aliases in _TYPE_ALIASES.values() for a in aliases}
    | {"course", "unit", "module", "session", "topic", "part"},
    key=len, reverse=True,
)
_TITLE_PREFIX = re.compile(
    r"^\s*(?:" + "|".join(re.escape(t) for t in _TITLE_TYPES) + r")(?:[\s_\-]*0*\d+|[\s_\-]+)[\s_\-]*",
    re.I,
)
_CN_PREFIX = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十]+\s*[讲课章周][\s_\-]*")


def _title_part(stem):
    s = _TITLE_PREFIX.sub("", stem)
    s = _CN_PREFIX.sub("", s)
    s = re.sub(r"[\s_\-]*\(\d+\)\s*$", "", s)
    return s.strip()


def _title_hit(query, stem):
    title = _title_part(stem)
    if len(title) < 3 or title.casefold() in _GENERIC_TITLE:
        return False
    pattern = r"[\s_.\-]*".join(re.escape(p) for p in re.split(r"[\s_.\-]+", title) if p)
    return bool(pattern) and bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", query, re.I))


def match_named_docs(query, docs):
    """docs: 可迭代 (id, filename)。返回问题里点名命中的 id 列表。"""
    query_keys = _identifier_keys(query)
    framed = bool(_FRAMING.search(query))
    hits = []
    for doc_id, filename in docs:
        stem = Path(filename).stem
        if ((_identifier_keys(stem) & query_keys)
                or _full_name_hit(query, stem)
                or re.search(rf"(?<![a-z0-9]){re.escape(filename)}", query, re.I)
                or (framed and _title_hit(query, stem))):
            hits.append(doc_id)
    return hits


def resolve_named_docs(query, project_id, db):
    """只在已完成文档里按名字解析;返回命中 id 列表([]/一份/多份)。"""
    rows = db.execute(
        select(Document.id, Document.filename)
        .where(Document.project_id == project_id, Document.status == "completed")
        .order_by(Document.id)
    ).all()
    return match_named_docs(query, [(r.id, r.filename) for r in rows])


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
    matched_ids = set(match_named_docs(query, [(doc.id, doc.filename) for doc in documents]))
    matches = [doc for doc in documents if doc.id in matched_ids]
    # Avoid converting a topic summary (e.g. 总结 SQL) into a whole-file overview.
    generic = re.search(r"这(?:份|个|些|篇)|文档|课件|全文|文件|ppt|pdf|document|slides?|file", query, re.I)
    bare = re.fullmatch(
        r"\s*(?:请|帮我|请帮我)?(?:(?:总结|概述|概括|梳理)(?:一下)?|"
        r"(?:主要)?讲了?什么|有哪些知识点|主要内容是什么)[。！？?!.\s]*", query
    )
    named = bool(_identifier_keys(query)) or re.search(
        r"《.+?》|\S+\.(?:pptx|pdf|docx|txt)\b", query, re.I
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

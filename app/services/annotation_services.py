"""把 AI 摘要写成文件内部的批注（Word 批注 / PDF 便签注释 / PPT 备注）。

设计要点：
1. 原文件永不修改，所有批注写在 data/annotated/ 下的副本里；
2. 副本是“投影”——每次变更都从原文件重新生成并重放数据库里的全部批注记录，
   因此删除记录即撤销批注，也不会因为反复追加而写坏文件；
3. 定位沿用前端高亮那套清洗规则（去注释/列表符/markdown/LaTeX），否则文件名对不上。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
from copy import deepcopy
from pathlib import Path

import pymupdf
from docx import Document as load_docx
from docx.text.run import Run
from sqlalchemy import text
from pptx import Presentation

logger = logging.getLogger(__name__)

ANNOTATED_ROOT = Path("data/annotated")
AUTHOR = "BNBU Agent"
INITIALS = "AI"
KIND_DOCX = "docx_comment"
KIND_PDF = "pdf_note"
KIND_PPTX = "pptx_note"

MIN_LINE_CHARS = 8
MAX_PDF_LINES = 12
MAX_ANCHOR_CHARS = 20000

_schema_ready = False
_schema_lock = threading.Lock()
_file_locks: dict[int, threading.Lock] = {}
_file_locks_guard = threading.Lock()


# ---------------------------------------------------------------- 基础设施

def ensure_schema() -> None:
    """只建自己这张表，避免动到既有表结构。"""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from app.db.database import engine
        from app.models.document_annotation import DocumentAnnotation

        DocumentAnnotation.__table__.create(bind=engine, checkfirst=True)
        # 旧库补列：turn_key 用于判断“新一轮回答是否覆盖旧批注”
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE document_annotation ADD COLUMN IF NOT EXISTS turn_key VARCHAR(64)"
            ))
        _schema_ready = True


def document_lock(document_id: int) -> threading.Lock:
    with _file_locks_guard:
        lock = _file_locks.get(document_id)
        if lock is None:
            lock = threading.Lock()
            _file_locks[document_id] = lock
        return lock


def anchor_fingerprint(anchor_text: str) -> str:
    return hashlib.sha256(normalize(anchor_text).encode("utf-8")).hexdigest()


def annotated_path(document) -> Path:
    original = Path(document.storage_path)
    out_dir = ANNOTATED_ROOT.resolve() / f"project_{document.project_id}"
    return out_dir / f"{document.id}_{original.stem}{original.suffix.lower()}"


def annotated_filename(document) -> str:
    original = Path(document.filename)
    return f"{original.stem}_AI批注{original.suffix.lower()}"


def kind_for_suffix(suffix: str) -> str | None:
    suffix = suffix.lower()
    if suffix == ".docx":
        return KIND_DOCX
    if suffix == ".pdf":
        return KIND_PDF
    if suffix in {".ppt", ".pptx"}:
        return KIND_PPTX
    return None


# ---------------------------------------------------------------- 文本清洗

def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def clean_lines(text: str) -> list[str]:
    """把引用原文切成可独立定位的行，清洗掉渲染时不会出现的标记。"""
    cleaned = (text or "")[:MAX_ANCHOR_CHARS]
    cleaned = re.sub(r"<!--[\s\S]*?-->", " ", cleaned)
    cleaned = re.sub(r"^[ \t]*(?:[-*•·]|#{1,6})[ \t]+", " ", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\\([_&%#{}$])", r"\1", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\*?", " ", cleaned)
    cleaned = cleaned.replace("$", "").replace("\\", "").replace("`", "").replace("*", "")
    cleaned = cleaned.replace("{", "").replace("}", "")
    out: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip(" \t|")
        if len(normalize(line)) < MIN_LINE_CHARS:
            continue
        # 超长段落按句切，避免单行搜索跨太多行
        if len(line) > 160:
            for piece in re.split(r"(?<=[。！？；.!?;])", line):
                piece = piece.strip()
                if len(normalize(piece)) >= MIN_LINE_CHARS:
                    out.append(piece)
        else:
            out.append(line)
    # 去重但保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for line in out:
        key = normalize(line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(line)
    return unique


def clause_variants(line: str) -> list[str]:
    """整行找不到时降级为逗号/分号分句，再退到行首片段。"""
    parts = [p.strip() for p in re.split(r"[，,；;、]", line) if len(normalize(p)) >= 10]
    parts.sort(key=lambda p: len(normalize(p)), reverse=True)
    tails = [line[:80].strip()] if len(normalize(line)) > 80 else []
    return parts[:4] + tails


# ---------------------------------------------------------------- 摘要生成

_SUMMARY_PROMPT = (
    "你在为一份学习资料写批注。根据【问题】【原文片段】【回答片段】，"
    "写一句结论式批注，说明这段原文在回答这个问题时起了什么作用。\n"
    "要求：\n"
    "1. 只用一句话，不超过 60 字，不要分段、不要列表；\n"
    "2. 语言与【问题】一致（问题中文就用中文）；\n"
    "3. 只陈述原文里能核实的内容，不要推测、不要罗列原文细节；\n"
    "4. 直接输出这句话，不要加“结论：”“批注：”之类前缀。"
)

_summarizer = None
_summarizer_lock = threading.Lock()


def _get_summarizer():
    global _summarizer
    if _summarizer is None:
        with _summarizer_lock:
            if _summarizer is None:
                from langchain_openai import ChatOpenAI

                _summarizer = ChatOpenAI(
                    model="glm-4.5-flash",
                    base_url="https://open.bigmodel.cn/api/paas/v4/",
                    api_key=os.environ["ZHIPU_API_KEY"],
                    temperature=0.2,
                )
    return _summarizer


def build_summary(question: str, anchor_text: str, answer: str) -> str:
    fallback = "原文要点：" + re.sub(r"\s+", " ", anchor_text or "").strip()[:60]
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        reply = _get_summarizer().invoke(
            [
                SystemMessage(content=_SUMMARY_PROMPT),
                HumanMessage(
                    content=(
                        f"【问题】{question[:500]}\n"
                        f"【原文片段】{(anchor_text or '')[:1500]}\n"
                        f"【回答片段】{(answer or '')[:1500]}"
                    )
                ),
            ]
        )
        text = (reply.content or "").strip() if isinstance(reply.content, str) else ""
        text = text.strip().strip("「」\"' “”").splitlines()[0].strip() if text else ""
        if len(text) > 120:
            text = text[:120].rstrip("，、。") + "。"
        return text or fallback
    except Exception as exc:  # 摘要失败不能阻断写批注
        logger.warning("annotation_summary_failed error_type=%s", type(exc).__name__)
        return fallback


# ---------------------------------------------------------------- 副本生成

def rebuild_copy(document, rows) -> Path | None:
    """从原文件重新生成带批注的副本；没有批注记录时清理副本。"""
    original = Path(document.storage_path)
    target = annotated_path(document)
    if not rows:
        if target.exists():
            target.unlink(missing_ok=True)
        return None
    if not original.is_file():
        raise FileNotFoundError("原文件不存在")

    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.with_name(f".{target.stem}.building{target.suffix}")
    shutil.copy2(original, work)
    try:
        suffix = target.suffix.lower()
        if suffix == ".docx":
            _apply_docx(work, rows)
        elif suffix == ".pdf":
            _apply_pdf(work, rows)
        elif suffix in {".ppt", ".pptx"}:
            _apply_pptx(work, rows)
        else:
            raise ValueError("该格式不支持写入批注")
        os.replace(work, target)
    finally:
        if work.exists():
            work.unlink(missing_ok=True)
    return target


# ---------------------------------------------------------------- Word 批注

def _iter_paragraphs(doc):
    """遍历正文、表格单元格以及文本框里的所有段落（文本框不在 doc.paragraphs 里）。"""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    for element in doc.element.body.iter(qn("w:p")):
        yield Paragraph(element, doc._body)


def _paragraph_haystack(paragraph):
    """返回（归一化文本，每个字符对应的 (run 下标, run 内下标)）。"""
    chars: list[tuple[int, int]] = []
    buf: list[str] = []
    for run_index, run in enumerate(paragraph.runs):
        for char_index, char in enumerate(run.text or ""):
            if char.isspace():
                continue
            chars.append((run_index, char_index))
            buf.append(char.lower())
    return "".join(buf), chars


def _split_run(run: Run, at: int) -> Run:
    """在 run 内部第 at 个字符处切开，返回后半段的新 run（格式沿用原 run）。"""
    text = run.text or ""
    if at <= 0 or at >= len(text):
        return run
    new_element = deepcopy(run._r)
    run._r.addnext(new_element)
    new_run = Run(new_element, run._parent)
    run.text = text[:at]
    new_run.text = text[at:]
    return new_run


def _locate(paragraph, needle: str):
    needle_n = normalize(needle)
    if len(needle_n) < MIN_LINE_CHARS:
        return None
    haystack, chars = _paragraph_haystack(paragraph)
    pos = haystack.find(needle_n)
    if pos < 0:
        return None
    start_run, start_char = chars[pos]
    end_run, end_char = chars[pos + len(needle_n) - 1]
    return start_run, start_char, end_run, end_char


def _comment_runs(paragraph, hit):
    start_run_index, start_char, end_run_index, end_char = hit
    runs = paragraph.runs
    if end_run_index >= len(runs):
        return None
    # 先切尾（只在末尾 run 内部切，不影响前面的下标）
    _split_run(runs[end_run_index], end_char + 1)
    end_run = runs[end_run_index]
    if start_run_index == end_run_index:
        middle = _split_run(runs[start_run_index], start_char)
        return (middle, middle) if (middle.text or "").strip() else None
    start_run = _split_run(runs[start_run_index], start_char)
    if not (start_run.text or "").strip() or not (end_run.text or "").strip():
        return None
    return start_run, end_run


def _apply_docx(path: Path, rows) -> None:
    doc = load_docx(str(path))
    paragraphs = list(_iter_paragraphs(doc))
    applied = 0
    for row in rows:
        runs = None
        for line in clean_lines(row.anchor_text):
            candidates = [line] + clause_variants(line)
            for needle in candidates:
                for paragraph in paragraphs:
                    hit = _locate(paragraph, needle)
                    if hit is None:
                        continue
                    runs = _comment_runs(paragraph, hit)
                    if runs:
                        break
                if runs:
                    break
            if runs:
                break
        if runs:
            doc.add_comment(list(runs), text=row.summary, author=AUTHOR, initials=INITIALS)
            applied += 1
        else:
            # 定位失败也要让用户看到这条批注：挂到首段并标记
            fallback = next((p for p in paragraphs if (p.text or "").strip()), None)
            if fallback is not None and fallback.runs:
                doc.add_comment(
                    fallback.runs[0],
                    text=f"（原文位置未精确定位）{row.summary}",
                    author=AUTHOR,
                    initials=INITIALS,
                )
                applied += 1
            else:
                logger.warning("docx_comment_unlocated document=%s", path.name)
    logger.info("docx_annotations_applied file=%s rows=%s applied=%s", path.name, len(rows), applied)
    doc.save(str(path))


# ---------------------------------------------------------------- PDF 批注

def _page_order(pages, page_count: int) -> list[int]:
    ordered: list[int] = []
    for page in pages or []:
        try:
            index = int(page) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < page_count and index not in ordered:
            ordered.append(index)
    rest = [i for i in range(page_count) if i not in ordered]
    return ordered + rest


def _apply_pdf(path: Path, rows) -> None:
    doc = pymupdf.open(str(path))
    try:
        for row in rows:
            lines = clean_lines(row.anchor_text)[:MAX_PDF_LINES]
            page_hits: dict[int, list] = {}
            for line in lines:
                needle = line
                found = None
                for page_index in _page_order(row.pages, doc.page_count):
                    rects = doc[page_index].search_for(needle)
                    if rects:
                        found = (page_index, rects)
                        break
                if found is None:
                    for variant in clause_variants(line):
                        for page_index in _page_order(row.pages, doc.page_count):
                            rects = doc[page_index].search_for(variant)
                            if rects:
                                found = (page_index, rects)
                                break
                        if found:
                            break
                if found:
                    page_hits.setdefault(found[0], []).extend(found[1])
            if not page_hits:
                logger.warning("pdf_annotation_unlocated file=%s", path.name)
                continue
            # 只在首个命中位置放一个便签式注释（不涂高亮），图标贴在命中文字旁边
            first_page = next(iter(page_hits))
            rects = page_hits[first_page]
            page = doc[first_page]
            annot = page.add_text_annot(_note_point(page, rects[0]), row.summary, icon="Comment")
            if annot is None:
                continue
            annot.set_info(title=AUTHOR, subject="AI 批注", content=row.summary)
            annot.update()
        out = path.with_name(path.name + ".out.pdf")
        doc.save(str(out), garbage=3, deflate=True)
    finally:
        doc.close()
    os.replace(out, path)


def _note_point(page, rect) -> pymupdf.Point:
    """便签注释的落点：贴着命中文字那一行，且不压住正文。

    依次尝试：文字右侧 → 页面右缘 → 行下方；都不行就取第一候选。
    """
    margin = page.rect.x1 - 24
    candidates = [
        pymupdf.Point(min(margin, rect.x1 + 14), max(page.rect.y0 + 6, rect.y0)),
        pymupdf.Point(margin, max(page.rect.y0 + 6, rect.y0)),
        pymupdf.Point(margin, rect.y1 + 8),
    ]
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]

    def blocked(point: pymupdf.Point) -> bool:
        probe = pymupdf.Rect(point.x - 2, point.y - 2, point.x + 18, point.y + 18)
        return any(word.intersects(probe) for word in words)

    for candidate in candidates:
        if not blocked(candidate):
            return candidate
    return candidates[0]


# ---------------------------------------------------------------- PPT 备注

def _slides_in_order(prs, pages) -> list:
    total = len(prs.slides)
    ordered: list[int] = []
    for page in pages or []:
        try:
            index = int(page) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < total and index not in ordered:
            ordered.append(index)
    return [prs.slides[i] for i in ordered] + [
        prs.slides[i] for i in range(total) if i not in ordered
    ]


def _slide_matches(slide, lines) -> bool:
    try:
        text = normalize(
            "\n".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
        )
    except Exception:
        return False
    return any(normalize(line) in text for line in lines[:6])


def _apply_pptx(path: Path, rows) -> None:
    prs = Presentation(str(path))
    for row in rows:
        lines = clean_lines(row.anchor_text)
        slides = _slides_in_order(prs, row.pages)
        target = next((slide for slide in slides if _slide_matches(slide, lines)), None)
        if target is None:
            target = slides[0] if slides else None
        if target is None:
            logger.warning("pptx_note_unlocated file=%s", path.name)
            continue
        frame = target.notes_slide.notes_text_frame
        if frame is None:
            continue
        frame.add_paragraph().text = f"【AI 批注】{row.summary}"
    prs.save(str(path))

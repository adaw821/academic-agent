"""Section-based PDF parents and block-packed children, with source pages."""

from dataclasses import dataclass, field

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
from docling_core.types.doc import DoclingDocument, ListItem, SectionHeaderItem, TitleItem

from app.services.ppt_chunking import markdown_blocks, pack_blocks


PDF_PARENT_SIZE = 4000
PDF_CHILD_SIZE = 800


@dataclass
class SourceBlock:
    text: str
    page_numbers: list[int]


@dataclass
class PdfParent:
    content: str
    section_id: int | None
    heading_path: list[str]
    page_numbers: list[int]
    children: list[SourceBlock] = field(default_factory=list)


def _pages(blocks: list[SourceBlock]) -> list[int]:
    return sorted({page for block in blocks for page in block.page_numbers})


def _pack(blocks: list[SourceBlock], budget: int) -> list[list[SourceBlock]]:
    return [
        [SourceBlock(text, blocks[index].page_numbers) for index, text in group]
        for group in pack_blocks([block.text for block in blocks], budget)
    ]


def _parts(serializer: MarkdownDocSerializer):
    for part in serializer.get_parts():
        items = part.get_unique_doc_items()
        if len(items) > 1 and all(isinstance(item, ListItem) for item in items):
            # A list group may span pages. Keep each item's own source pages.
            for item in items:
                yield serializer.serialize(item=item)
        else:
            yield part


def split_pdf_document(
    document: DoclingDocument,
    parent_size: int = PDF_PARENT_SIZE,
    child_size: int = PDF_CHILD_SIZE,
) -> list[PdfParent]:
    """Follow Docling headings in reading order; page boundaries do not end sections.

    Section IDs are document-local heading occurrence IDs, never title hashes.
    Source pages refer to Docling items; if an item spans pages, its pieces keep
    all those pages rather than inventing a more precise location.
    """
    if not document.pages:
        raise ValueError("PDF 解析结果缺少页码，无法保留来源页码")
    if parent_size <= 0 or child_size <= 0:
        raise ValueError("分片大小必须大于 0")

    serializer = MarkdownDocSerializer(
        # 与上传流程一致：保留图片的视觉识别描述，不遍历图片内部子节点。
        doc=document, params=MarkdownParams(traverse_pictures=False),
    )
    parents = []
    headings: list[tuple[int, str]] = []
    section_id = None
    heading_count = 0
    pending: list[SourceBlock] = []

    def flush():
        if not pending:
            return
        path = [title for _, title in headings]
        prefix = "# " + " > ".join(path) + "\n\n" if path else ""
        if len(prefix) >= min(parent_size, child_size):
            raise ValueError("PDF 完整标题路径过长，无法在分片字符上限内保留正文")
        for group in _pack(pending, parent_size - len(prefix)):
            children = [
                SourceBlock(prefix + "\n\n".join(b.text for b in child), _pages(child))
                for child in _pack(group, child_size - len(prefix))
            ]
            parents.append(PdfParent(
                content=prefix + "\n\n".join(b.text for b in group),
                section_id=section_id,
                heading_path=path.copy(),
                page_numbers=_pages(group),
                children=children,
            ))
        pending.clear()

    # Serializer parts preserve tables, lists and code, and expose their DocItems.
    for part in _parts(serializer):
        items = part.get_unique_doc_items()
        heading = items[0] if len(items) == 1 and isinstance(items[0], (TitleItem, SectionHeaderItem)) else None
        if heading is not None:
            title = " ".join(heading.text.split())
            if not title:
                continue
            flush()
            level = 0 if isinstance(heading, TitleItem) else heading.level
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            heading_count += 1
            section_id = heading_count
            continue

        blocks = markdown_blocks(part.text)
        if not blocks:
            continue
        pages = sorted({prov.page_no for item in items for prov in item.prov})
        if not pages:
            raise ValueError("PDF 正文缺少来源页码，无法保留来源信息")
        if not headings and pending and pending[-1].page_numbers != pages:
            # Text before the first heading, or an entirely unheaded PDF, stays page-local.
            flush()
        pending.extend(SourceBlock(block, pages) for block in blocks)
    flush()
    return parents

"""Content-block packing shared by PPT slides and PDF sections."""

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter


PPT_CHILD_SIZE = 800


def markdown_blocks(text: str) -> list[str]:
    """Keep paragraphs, list items and fenced code intact before packing."""
    blocks = []
    current = []
    fence = None

    def flush():
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in text.strip().splitlines():
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            current.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
                flush()
            continue
        if marker:
            flush()
            fence = marker[1]
            current.append(line)
        elif not stripped:
            flush()
        elif stripped == "<!-- image -->" or re.fullmatch(r"(?:[-*+]|\d+[.)])", stripped):
            # Empty list markers and image placeholders are not searchable evidence.
            continue
        else:
            if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)", line):
                flush()
            current.append(line)
    flush()
    return blocks


def clean_slide_text(text: str) -> str:
    return "\n\n".join(markdown_blocks(text))


def split_slide_text(text: str, chunk_size: int = PPT_CHILD_SIZE) -> list[str]:
    """Pack adjacent blocks within one slide; repeat a short leading title."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    blocks = markdown_blocks(text)
    if not blocks or all(re.fullmatch(r"#{1,6}\s+[^\n]+", block) for block in blocks):
        return []

    title = ""
    if re.fullmatch(r"#{1,6}\s+[^\n]+", blocks[0]) and len(blocks[0]) + 2 < chunk_size // 2:
        title = blocks.pop(0) + "\n\n"
    budget = chunk_size - len(title)
    return [title + "\n\n".join(part for _, part in group)
            for group in pack_blocks(blocks, budget)]


def pack_blocks(blocks: list[str], budget: int) -> list[list[tuple[int, str]]]:
    """Pack blocks without overlap, retaining source indices for PDF provenance."""
    if budget <= 0:
        raise ValueError("内容块的字符预算必须大于 0")
    fallback = RecursiveCharacterTextSplitter(
        chunk_size=budget,
        chunk_overlap=0,
        separators=[r"(?<=[.!?。！？])\s+", r"\s+", ""],
        is_separator_regex=True,
        keep_separator="end",
    )
    chunks = []
    current = []
    current_size = 0
    for index, block in enumerate(blocks):
        # Only oversized blocks are split, first at sentences, then words/characters.
        parts = [block] if len(block) <= budget else fallback.split_text(block)
        for part in parts:
            candidate_size = current_size + (2 if current else 0) + len(part)
            if candidate_size > budget:
                chunks.append(current)
                current = []
                current_size = 0
            current_size += (2 if current else 0) + len(part)
            current.append((index, part))
    if current:
        chunks.append(current)
    return chunks

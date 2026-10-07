"""Preview actual PPT parsing and chunking without a database or embedding calls."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docling.document_converter import DocumentConverter
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.ppt_chunking import clean_slide_text, split_slide_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, help="Output Markdown preview")
    args = parser.parse_args()
    document = DocumentConverter().convert(args.input).document
    original = document.export_to_markdown().strip()
    old_parents = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=80).split_text(original)
    old_counts = {}
    for size in (150, 300):
        splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=30)
        old_counts[size] = sum(len(splitter.split_text(p)) for p in old_parents)
    pages = []
    for page_no in sorted(document.pages):
        raw = document.export_to_markdown(page_no=page_no)
        parent = clean_slide_text(raw)
        children = split_slide_text(parent)
        pages.append(dict(page_no=page_no, parent=parent, children=children,
                          image_placeholders=raw.count("<!-- image -->")))
    summary = dict(slides=len(pages), old_parent_count=len(old_parents),
                   old_child_counts=old_counts,
                   new_parent_count=sum(bool(p["children"]) for p in pages),
                   new_child_count=sum(len(p["children"]) for p in pages))
    lines = [f"# {args.input.name} 分片预览", "", "仅离线解析与分片，未写入数据库、未生成向量。",
             "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", ""]
    for page in pages:
        lines.extend([f"## 第 {page['page_no']} 页", "",
                      f"子片数：{len(page['children'])}；图片占位符：{page['image_placeholders']}。", ""])
        if not page["children"]:
            lines.extend(["无可检索正文，跳过。解析内容：", "", page["parent"] or "（空）", ""])
        for index, child in enumerate(page["children"], 1):
            lines.extend([f"### 子片 {index}（{len(child)} 字符）", "", "````markdown", child, "````", ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(dict(summary=summary, pages=pages),
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"Preview: {args.output.resolve()}")


if __name__ == "__main__":
    main()

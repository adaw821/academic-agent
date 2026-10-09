"""LangGraph topology for synchronous, request-local workflows.

Nodes run sequentially because they share a SQLAlchemy session. Graphs have no
checkpointer or automatic node retries: transaction ownership remains with the
API/background task, and a failed write must not be replayed implicitly.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.performance import timed_stage


class QAState(TypedDict, total=False):
    doc_ids: list[int] | None
    selection: dict | None
    history: list[dict]
    summary: str
    summary_boundary: int | None
    search_query: str
    translated_query: str | None
    original_candidates: list[dict]
    translated_candidates: list[dict]
    final_chunks: list[dict]
    result: dict


class DocumentState(TypedDict, total=False):
    text: str
    document: Any
    pictures: Any
    image_records: list[dict]
    page_texts: list[str] | None
    pdf_document: Any
    parse_ms: float
    stats: dict
    classifications: list[dict] | None


def build_qa_graph(nodes):
    graph = StateGraph(QAState)
    for name, node in nodes.items():
        graph.add_node(name, timed_stage(f"graph.qa.{name}")(node))
    sequence = [START, "validate", "history", "rewrite", "translate"]
    for source, target in zip(sequence, sequence[1:]):
        graph.add_edge(source, target)
    graph.add_conditional_edges("translate", lambda state: (
        "selection_retrieve" if state.get("selection") is not None else "retrieve_original"
    ), ["selection_retrieve", "retrieve_original"])
    graph.add_conditional_edges("retrieve_original", lambda state: (
        "retrieve_translated" if state.get("translated_query") else "rerank"
    ), ["retrieve_translated", "rerank"])
    graph.add_edge("retrieve_translated", "rerank")
    graph.add_edge("rerank", "answer")
    graph.add_edge("selection_retrieve", "answer")
    graph.add_edge("answer", "save")
    graph.add_edge("save", END)
    return graph.compile()


def build_document_graph(nodes, *, is_txt, needs_images):
    graph = StateGraph(DocumentState)
    for name, node in nodes.items():
        graph.add_node(name, timed_stage(f"graph.document.{name}")(node))
    graph.add_conditional_edges(START, lambda state: "decode" if is_txt else "convert", ["decode", "convert"])
    graph.add_edge("decode", "split_embed_store")
    graph.add_conditional_edges("convert", lambda state: (
        "extract_images" if needs_images else "export"
    ), ["extract_images", "export"])
    graph.add_edge("extract_images", "images_type_router")
    graph.add_edge("images_type_router", "enrich_images")
    graph.add_edge("enrich_images", "export")
    graph.add_edge("export", "split_embed_store")
    graph.add_edge("split_embed_store", END)
    return graph.compile()

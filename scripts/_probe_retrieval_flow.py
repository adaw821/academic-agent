"""一次性诊断：盯着一条"应该命中"的内容，看它在问答漏斗的哪一级消失。

用法：改下面 CONFIG 几个值，从仓库根目录跑：
    python -m scripts._probe_retrieval_flow

只读不写，不碰生产代码，不调答案生成的大模型。用完可删。
路由里 overview / tool 两条是 LLM 判的，本脚本不替你调模型，只提示"生产可能在此拐走"。
"""

from app.config import load_config

load_config()  # ★必须最先：先加载 .env，后面 import app.xxx 才不炸

from app.db.database import SessionLocal
# 和 main.py 一样把全部模型导一遍，否则 ORM 关系（如 Document→Project）配不出来，首个查询就炸。
from app.models.users import User
from app.models.courses import Course
from app.models.assignments import Assignment
from app.models.course_resources import CourseResource
from app.models.course_timetable import CourseSchedule
from app.models.projects import Project
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child
from app.services.search_services import search_vector, search_bm25, rrf_fuse, rerank_results
from app.services.document_overview import resolve_named_docs, read_document_overview
from app.services.context_management import count_message_tokens, is_over_budget
from app.services.answer_services import CONTEXT_LIMIT, RESERVED_OUTPUT_TOKENS, SAFETY_MARGIN

from sqlalchemy import select, func

# ============================ CONFIG ============================
PROJECT_ID = 1
QUERY = "详细讲反证法"
EXPECT_KEYWORD = "反证法"  # 用来在 DB 里定位"正确内容"
PCT = 0.4                 # 和接口默认一致 schemas/search.py:17
DOC_IDS = None            # 勾选了资料就填 [id, ...]，否则 None
DOCUMENT_ID = None        # 划词/指定单份文档才填，否则 None
# ===============================================================


def hr(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def hit_report(label, rows, id_key, score_key, target_ids):
    """打印某一级的命中情况：前几名是谁 + target 在不在、排第几。"""
    print(f"[{label}] 共 {len(rows)} 条，前 10：")
    for rank, r in enumerate(rows[:10], start=1):
        mark = "  <<< target" if r[id_key] in target_ids else ""
        print(f"    #{rank:<3} child={r[id_key]:<8} {score_key}={round(float(r[score_key]), 4)}{mark}")
    found = [(i, r[id_key]) for i, r in enumerate(rows, start=1) if r[id_key] in target_ids]
    if found:
        print(f"    target 命中：{[f'第{rank}名(child={cid})' for rank, cid in found]}")
    else:
        print("    target：未命中 ❌")
    return bool(found)


def main():
    db = SessionLocal()
    try:
        # ── 截断点 0：路由到哪了 ──
        hr("截断点 0 · 路由判定（确定性部分）")
        scope_ids = DOC_IDS
        named = False
        selection = DOCUMENT_ID is not None

        if selection:
            print(f"selection=True（指定了 document_id={DOCUMENT_ID}）→ 走 selection_retrieve(search_document_first)")
        else:
            if scope_ids is None:
                hit = resolve_named_docs(QUERY, PROJECT_ID, db)
                if hit:
                    scope_ids, named = hit, True
                    print(f"点名命中 → doc_ids={scope_ids}")
                else:
                    print("点名未命中（没从问题里解析出具体文档）")

            if named and len(scope_ids) > 1:
                print("route = clarify（点名命中多份，生产会反问让你勾选）")
                print("→ 不走检索，下面几级无意义。")
                return

            if scope_ids and len(scope_ids) == 1:
                chunks = read_document_overview(PROJECT_ID, scope_ids, db)
                if chunks:
                    msg = [{"role": "user", "content": "\n".join(c["parent_content"] for c in chunks)}]
                    over = is_over_budget(count_message_tokens(msg) + 2000,
                                          CONTEXT_LIMIT, RESERVED_OUTPUT_TOKENS, SAFETY_MARGIN)
                    if not over:
                        print(f"route = whole_doc（单份 doc={scope_ids[0]} 整篇装得下，{len(chunks)} 个父片直接读全文）")
                        print("→ 不走 RRF/rerank，下面三级无意义。")
                        return
                    print(f"单份 doc={scope_ids[0]} 但整篇装不下 → 继续往下判")

            print("确定性判定未拐走。注意：生产接下来还会用 LLM 判 overview / tool 两条路，")
            print("本脚本不调模型。想确认真实落点，看服务日志里的 event=qa_route route=xxx。")
            print(f"→ 假设 route = continue，用 doc_ids={scope_ids} 继续看检索漏斗。")

        # 检索用的范围：点名收紧过就用 scope_ids
        retrieve_doc_ids = scope_ids

        # ── 先在 DB 里定位"正确内容" ──
        hr("定位 target · 关键词在 DB 命中的 child")
        filters = [Document.project_id == PROJECT_ID, Document.status == "completed"]
        if retrieve_doc_ids is not None:
            filters.append(Document.id.in_(retrieve_doc_ids))

        child_hit = db.execute(
            select(Child.id, Child.content, Parent.id.label("parent_id"))
            .join(Parent, Parent.id == Child.parent_id)
            .join(Document, Document.id == Parent.document_id)
            .where(*filters, Child.content.ilike(f"%{EXPECT_KEYWORD}%"))
        ).all()
        target_ids = {row.id for row in child_hit}
        print(f"child 命中 {len(target_ids)} 条：{sorted(target_ids)}")

        parent_only = db.scalar(
            select(func.count(Parent.id))
            .join(Document, Document.id == Parent.document_id)
            .where(*filters, Parent.content.ilike(f"%{EXPECT_KEYWORD}%"))
        )
        print(f"parent 命中 {parent_only} 条。")
        if parent_only and not target_ids:
            print("⚠ 只有 parent 命中、child 没命中 → 内容被切碎/落在 child 边界，是 chunking 问题，")
            print("  在召回上游，下面调召回没用。")

        # ── 截断点 1：召回之后 ──
        hr("截断点 1 · 向量 / BM25 召回")
        vec = search_vector(QUERY, PROJECT_ID, db, PCT, doc_ids=retrieve_doc_ids)
        bm = search_bm25(QUERY, PROJECT_ID, db, PCT, doc_ids=retrieve_doc_ids)
        in_vec = hit_report("vector", vec, "child_id", "distance", target_ids)
        in_bm = hit_report("bm25", bm, "child_id", "bm25_score", target_ids)
        if not in_vec and not in_bm:
            print("\n→ 病灶在召回：两路都没捞到 target。看 embedding / 分词 / PCT 调大。下面级无意义。")

        # ── 截断点 2：RRF 融合之后（这里砍到 10）──
        hr("截断点 2 · RRF 融合（merged[:10]）")
        merged = rrf_fuse(vec, bm)
        in_rrf = hit_report("rrf", merged, "child_id", "ranking_score", target_ids)
        if (in_vec or in_bm) and not in_rrf:
            print("\n→ 病灶在 RRF：target 召回到了，但被 merged[:10] 砍掉。放宽那个 10。")

        # ── 截断点 3：rerank + 过滤之后（最终喂大模型的）──
        hr("截断点 3 · rerank + chunk_filter（min_score=0.3, top_k=5）")
        final = rerank_results(QUERY, None, merged, None)
        print(f"[final] 最终 {len(final)} 片喂给大模型：")
        for i, r in enumerate(final, start=1):
            mark = "  <<< target" if r["child_id"] in target_ids else ""
            print(f"    #{i} child={r['child_id']} parent={r['parent_id']} "
                  f"score={round(r['total_score'], 3)}{mark}")
        in_final = any(r["child_id"] in target_ids for r in final)
        if in_rrf and not in_final:
            print("\n→ 病灶在 rerank/过滤：target 进了 rerank 但没活下来。")
            print("  要么 rerank 分 < 0.3，要么被 top_k=5 / 一父一子挤掉。")
            print("  想看它到底打几分，单独拿 get_reranker() 给 [QUERY, target_content] 打一次。")
        elif in_final:
            print("\n→ target 活到了最后。若答案仍不对，是生成问题，不是检索。")
    finally:
        db.close()


if __name__ == "__main__":
    main()

import os
T = r"D:\wangqiaopei\Desktop\academic-agent\ai-service\evaluate_rag.py"
ds = [
    ('\u6570\u636e\u5e93\u7ba1\u7406\u7cfb\u7edf\u662f\u4ec0\u4e48', '\u6570\u636e\u5e93\u7ba1\u7406\u7cfb\u7edf\u662f\u4f4d\u4e8e\u7528\u6237\u4e0e\u64cd\u4f5c\u7cfb\u7edf\u4e4b\u95f4\u7684\u6570\u636e\u7ba1\u7406\u8f6f\u4ef6'),
    ('\u5173\u7cfb\u6a21\u578b\u7531\u54ea\u4e09\u90e8\u5206\u7ec4\u6210', '\u5173\u7cfb\u6570\u636e\u7ed3\u6784\u3001\u5173\u7cfb\u64cd\u4f5c\u96c6\u5408\u3001\u5173\u7cfb\u5b8c\u6574\u6027\u7ea6\u675f'),
    ('SQL\u8bed\u8a00\u5206\u4e3a\u54ea\u56db\u7c7b', 'DDL\u3001DML\u3001DQL\u3001DCL'),
    ('\u4e3b\u952e\u5916\u952e\u662f\u4ec0\u4e48', '\u4e3b\u952e\u552f\u4e00\u6807\u8bc6\u8bb0\u5f55\u3001\u5916\u952e\u5efa\u7acb\u4e24\u8868\u8054\u7cfb'),
    ('ACID\u56db\u5927\u7279\u6027\u662f\u4ec0\u4e48', '\u539f\u5b50\u6027\u3001\u4e00\u81f4\u6027\u3001\u9694\u79bb\u6027\u3001\u6301\u4e45\u6027'),
    ('\u7d22\u5f15\u6709\u4ec0\u4e48\u4f18\u7f3a\u70b9', '\u7d22\u5f15\u52a0\u5feb\u67e5\u8be2\u4f46\u51cf\u6162\u5199\u5165\u5e76\u5360\u7528\u7a7a\u95f4'),
    ('MySQL\u9ed8\u8ba4\u9694\u79bb\u7ea7\u522b', '\u53ef\u91cd\u590d\u8bfb Repeatable Read'),
    ('EXPLAIN\u547d\u4ee4\u6709\u4ec0\u4e48\u7528', '\u67e5\u770bSQL\u6267\u884c\u8ba1\u5212\u7528\u4e8e\u5206\u6790\u6162\u67e5\u8be2'),
    ('\u8bfe\u7a0b\u8003\u6838\u65b9\u5f0f\u662f\u4ec0\u4e48', '\u5e73\u65f6\u4f5c\u4e1a30\u52a0\u671f\u4e2d20\u52a0\u671f\u672b50'),
    ('JOIN\u6709\u54ea\u4e9b\u7c7b\u578b', '\u5185\u8fde\u63a5\u3001\u5de6\u5916\u8fde\u63a5\u3001\u53f3\u5916\u8fde\u63a5'),
    ('Python\u5217\u8868\u5143\u7ec4\u533a\u522b', '\u8d44\u6599\u4e2d\u672a\u63d0\u53ca'),
    ('\u673a\u5668\u5b66\u4e60\u8fc7\u62df\u5408', '\u8d44\u6599\u4e2d\u672a\u63d0\u53ca'),
]
L = []
a = L.append
a('import json, time, requests')
a('from ragas.metrics import context_precision, context_recall, faithfulness, answer_relevancy, answer_similarity')
a('from ragas import evaluate')
a('from ragas.dataset_schema import SingleTurnSample')
a('from langchain_openai import ChatOpenAI')
a('')
a('RAG_URL = "http://127.0.0.1:5000"')
a('GLM_KEY = "4dcb28b18ef64861a154b6350aa35637.ZRxnmCjMWnEEfXwo"')
a('GLM_BASE = "https://open.bigmodel.cn/api/paas/v4/"')
a('judge_llm = ChatOpenAI(model="glm-4-flash", api_key=GLM_KEY, base_url=GLM_BASE)')
a('EVAL_DATASET = [')
for q, gt in ds:
    a('    {"q": "' + q + '", "gt": "' + gt + '"},')
a(']')
a('')
a('def rag_ask(q, top_k=5):')
a('    r = requests.post(RAG_URL + "/api/rag/ask", json={"question": q, "top_k": top_k}, timeout=60)')
a('    return r.json()')
a('')
a('def main():')
a('    print("=== RAG Evaluation (Ragas) ===")')
a('    try:')
a('        requests.get(RAG_URL, timeout=5)')
a('        print("RAG service: OK")')
a('    except Exception as e:')
a('        print(f"RAG NOT reachable: {e}")')
a('        print("Start RAG service first on port 5000")')
a('        return')
a('    samples = []')
a('    for i, item in enumerate(EVAL_DATASET, 1):')
a('        q, gt = item["q"], item["gt"]')
a('        print(f"\\n[{i}/{len(EVAL_DATASET)}] {q}")')
a('        try:')
a('            rag = rag_ask(q)')
a('            answer = rag.get("answer", "")')
a('            sources = rag.get("sources", [])')
a('        except Exception as ex:')
a('            print(f"    [E] RAG: {ex}")')
a('            continue')
a('        print(f"    Retrieved {len(sources)} chunks, answer: {answer[:50]}...")')
a('        ctx = [sources] if sources else [[]]')
a('        s = SingleTurnSample(user_input=q, response=answer, references=[gt], retrieved_contexts=ctx)')
a('        samples.append(s)')
a('    print(f"\\nTotal samples: {len(samples)}")')
a('    if not samples:')
a('        print("No samples")')
a('        return')
a('    metrics = [context_precision, context_recall, faithfulness, answer_relevancy, answer_similarity]')
a('    print("Running ragas evaluate with 5 metrics...")')
a('    result = evaluate(samples, metrics=metrics, llm=judge_llm)')
a('    print(result)')
a('    df = result.to_pandas()')
a('    df.to_csv("rag_eval_results.csv", index=False, encoding="utf-8-sig")')
a('    print("Saved to rag_eval_results.csv")')
a('')
a('if __name__ == "__main__":')
a('    main()')
with open(T, 'w', encoding='utf-8') as f:
    f.write('\\n'.join(L))
print(f'Written {len(L)} lines to {T}')
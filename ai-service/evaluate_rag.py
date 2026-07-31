import json, time, requests
from openai import OpenAI

RAG_URL = "http://127.0.0.1:5000"
GLM_KEY = "4dcb28b18ef64861a154b6350aa35637.ZRxnmCjMWnEEfXwo"
GLM_BASE = "https://open.bigmodel.cn/api/paas/v4/"
JUDGE_MODEL = "glm-4-flash"
client = OpenAI(api_key=GLM_KEY, base_url=GLM_BASE)

EVAL_DATASET = [
    {"q": "数据库管理系统是什么", "gt": "数据库管理系统是位于用户与操作系统之间的数据管理软件"},
    {"q": "关系模型由哪三部分组成", "gt": "关系数据结构、关系操作集合、关系完整性约束"},
    {"q": "SQL语言分为哪四类", "gt": "DDL、DML、DQL、DCL"},
    {"q": "主键外键是什么", "gt": "主键唯一标识记录、外键建立两表联系"},
    {"q": "ACID四大特性是什么", "gt": "原子性、一致性、隔离性、持久性"},
    {"q": "索引有什么优缺点", "gt": "索引加快查询但减慢写入并占用空间"},
    {"q": "MySQL默认隔离级别", "gt": "可重复读 Repeatable Read"},
    {"q": "EXPLAIN命令有什么用", "gt": "查看SQL执行计划用于分析慢查询"},
    {"q": "课程考核方式是什么", "gt": "平时作业30加期中20加期末50"},
    {"q": "JOIN有哪些类型", "gt": "内连接、左外连接、右外连接"},
    {"q": "Python列表元组区别", "gt": "资料中未提及"},
    {"q": "机器学习过拟合", "gt": "资料中未提及"},
]

METRIC_PROMPTS = {
    "context_precision": "RAG评估专家 判断检索资料对问题是否相关 问题:{question} 资料:{contexts} 只返回JSON:{{score:0-1,reason:理由}} 1.0全部相关 0.7大部分 0.4部分 0.0无关",
    "context_recall": "RAG评估专家 判断检索资料是否覆盖参考答案 参考答案:{ground_truth} 资料:{contexts} 只返回JSON:{{score:0-1,reason:理由}} 1.0完全覆盖 0.7大部分 0.4部分 0.0未覆盖",
    "faithfulness": "RAG评估专家 判断答案是否忠实于资料无编造 资料:{contexts} 答案:{answer} 只返回JSON:{{score:0-1,reason:理由}} 1.0完全基于资料 0.7大部分 0.4部分无依据 0.0大量编造",
    "answer_relevancy": "RAG评估专家 判断答案是否切题 问题:{question} 答案:{answer} 只返回JSON:{{score:0-1,reason:理由}} 1.0完全切题 0.7基本切题 0.4部分偏题 0.0完全偏题",
    "answer_similarity": "RAG评估专家 判断答案与参考答案语义一致性 参考答案:{ground_truth} 模型答案:{answer} 只返回JSON:{{score:0-1,reason:理由}} 1.0完全一致 0.7基本一致 0.4部分一致 0.0完全不同",
}

METRIC_KEYS = list(METRIC_PROMPTS.keys())
METRIC_LABELS = {"context_precision": "上下文精准度", "context_recall": "上下文员回率", "faithfulness": "答案忠实度", "answer_relevancy": "答案相关性", "answer_similarity": "答案相似度"}

def rag_ask(q, top_k=5):
    r = requests.post(RAG_URL + "/api/rag/ask", json={"question": q, "top_k": top_k}, timeout=60)
    return r.json()

def judge(prompt):
    try:
        resp = client.chat.completions.create(model=JUDGE_MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}])
        text = resp.choices[0].message.content.strip()
        s = text.find("{")
        e = text.rfind("}") + 1
        if s >= 0 and e > 0:
            data = json.loads(text[s:e])
            return float(data.get("score", 0)), data.get("reason", "")
    except Exception as ex:
        print(f"    [W] judge: {ex}")
    return 0.0, "judge error"

def main():
    print("=== RAG Evaluation (Ragas-style, no ragas dep) ===")
    try:
        requests.get(RAG_URL, timeout=5)
        print("RAG service: OK")
    except Exception as e:
        print(f"RAG NOT reachable: {e}")
        print("Start RAG service first: cd ai-service && python main.py")
        return
    samples = []
    for i, item in enumerate(EVAL_DATASET, 1):
        q, gt = item["q"], item["gt"]
        print(f"\n[{i}/{len(EVAL_DATASET)}] {q}")
        try:
            rag = rag_ask(q)
            answer = rag.get("answer", "")
            sources = rag.get("sources", [])
        except Exception as ex:
            print(f"    [E] RAG: {ex}")
            continue
        print(f"    Retrieved {len(sources)} chunks")
        ctx = chr(10).join(f"[{j+1}]" + s for j, s in enumerate(sources))
        scores = {}
        for name in METRIC_KEYS:
            tmpl = METRIC_PROMPTS[name]
            p = tmpl.format(question=q, contexts=ctx, answer=answer, ground_truth=gt)
            sc, reason = judge(p)
            scores[name] = {"score": sc, "reason": reason}
            label = METRIC_LABELS[name]
            print(f"    {label:12s} = {sc:.2f}  ({reason})")
            time.sleep(0.3)
        samples.append({"q": q, "gt": gt, "a": answer, "scores": scores})
    print(f"\nTotal: {len(samples)} samples")
    print("\n=== Overall ===")
    all_s = {n: [] for n in METRIC_KEYS}
    for s in samples:
        for n in METRIC_KEYS:
            all_s[n].append(s["scores"][n]["score"])
    for n in METRIC_KEYS:
        avg = sum(all_s[n]) / len(all_s[n]) if all_s[n] else 0
        bar = "#" * int(avg * 20) + "-" * (20 - int(avg * 20))
        label = METRIC_LABELS[n]
        print(f"  {label:12s} |{bar}| {avg:.2f}")
    all_avgs = [sum(all_s[n]) / len(all_s[n]) if all_s[n] else 0 for n in METRIC_KEYS]
    overall = sum(all_avgs) / len(all_avgs)
    print(f"\n  Overall: {overall:.2f} / 1.00")
    out = "rag_evaluation_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out}")

if __name__ == "__main__":
    main()
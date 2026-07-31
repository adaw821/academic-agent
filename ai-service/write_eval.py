import json, os

path = r"D:\wangqiaopei\Desktop\academic-agent\ai-service\evaluate_rag.py"

content = """\\\"\\\"\\\"RAG Quality Evaluation Script (Ragas-style metrics)

Uses GLM-4-flash as judge LLM to evaluate 5 core metrics:
  1. context_precision   - Are retrieved documents relevant to the question?
  2. context_recall       - Do retrieved documents cover the ground truth?
  3. faithfulness         - Is the answer faithful to retrieved documents?
  4. answer_relevancy     - Is the answer relevant to the question?
  5. answer_similarity    - How similar is the answer to ground truth?

Usage:
  1. Start RAG service: cd ai-service && python main.py  (port 5000)
  2. Ingest documents:  POST /api/rag/ingest
  3. Run evaluation:     ..\\\\agent\\\\Scripts\\\\python evaluate_rag.py
\\\"\\\"\\\"

import json
import time
import requests
from openai import OpenAI

# === Configuration ===
RAG_URL = "http://127.0.0.1:5000"
GLM_API_KEY = "4dcb28b18ef64861a154b6350aa35637.ZRxnmCjMWnEEfXwo"
GLM_BASE = "https://open.bigmodel.cn/api/paas/v4/"
JUDGE_MODEL = "glm-4-flash"

client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE)

# === Evaluation Dataset ===
EVAL_DATASET = [
    {
        "question": "\\u6570\\u636e\\u5e93\\u7ba1\\u7406\\u7cfb\\u7edf\\uff08DBMS\\uff09\\u662f\\u4ec0\\u4e48\\uff1f\\u5e38\\u89c1\\u7684\\u6709\\u54ea\\u4e9b\\uff1f",
        "ground_truth": "\\u6570\\u636e\\u5e93\\u7ba1\\u7406\\u7cfb\\u7edf\\u662f\\u4f4d\\u4e8e\\u7528\\u6237\\u4e0e\\u64cd\\u4f5c\\u7cfb\\u7edf\\u4e4b\\u95f4\\u7684\\u4e00\\u5c42\\u6570\\u636e\\u7ba1\\u7406\\u8f6f\\u4ef6\\uff0c\\u5e38\\u89c1\\u7684\\u6709 MySQL\\u3001PostgreSQL\\u3001Oracle\\u3002",
        "category": "\\u6982\\u5ff5\\u5b9a\\u4e49",
    },
"""

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"Part 1 written: {len(content)} chars")

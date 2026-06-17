import base64
import json
import os
import anthropic
import chromadb
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")          # 演示页:浏览器开 http://127.0.0.1:5000/ 就能看到
def home():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))
client = anthropic.Anthropic(
    api_key="sk-ICwd9A5ZvlLAiJ37CFgOFjNbOzqLIpEgLkM6sH2CREDIDzPi",                    # 换成你自己的 key
    base_url="https://api.yixia.ai",      # 中转地址
)

# 向量库:持久化到磁盘 ./chroma_db,重启不丢
# collection 相当于一张"向量表";add 文档时 Chroma 会用内置的本地小模型自动 embedding
chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection("lecture_notes")
#接收springboot传来的图
@app.post("/api/llm/parse_schedule")
#file跟springboot单子上填的一样 ...表示必须收到图片
async def parse_schedule(file: UploadFile = File(...)):
    image_bytes = await file.read()                                   # 1. 读出图片字节
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")  # 2. 转成 base64
    print(f"[DEBUG] 文件名={file.filename} content_type={file.content_type} 字节数={len(image_bytes)} b64长度={len(image_b64)}")

    response = client.messages.create(                                # 3. 调 Claude 看图
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64",
                            "media_type": file.content_type,
                            "data": image_b64}},
                {"type": "text", "text": """你是一个课程表解析助手。下面是一张课程表图片。
请提取图片中所有课程,以 JSON 数组的形式返回。

每条记录是一个对象,字段如下:
- courseName: 课程名称(字符串)
- course_id: 课程编号(字符串,没有就填 "")
- day_of_week: 星期几(整数,周一=1,周二=2,周三=3,周四=4,周五=5,周六=6,周日=7)
- start_time: 上课开始时间(字符串,24小时制,如 "09:00")
- end_time: 下课结束时间(字符串,24小时制,如 "10:50")
- location: 上课地点/教室(字符串,没有就填 "")

规则:
1. 只返回 JSON 数组本身,不要任何多余文字,不要解释,不要用代码块(```)包裹。
2. day_of_week 必须是数字,不能是 "周一" 这种文字。
3. 同一门课如果在不同的天上课,每一天单独拆成一条记录。
4. 同一门课在某一天连续上几节课,合并成一条:start_time 取最早,end_time 取最晚。
5. 找不到的字段一律填空字符串 ""。

示例输出:
[{"courseName":"高等数学","course_id":"MATH101","day_of_week":1,"start_time":"08:00","end_time":"09:40","location":"教学楼A101"},{"courseName":"大学英语","course_id":"ENG201","day_of_week":3,"start_time":"10:00","end_time":"11:40","location":"外语楼302"}]
"""},
            ],
        }],
    )

    text = response.content[0].text                # 拿到模型回复(不再用 prefill)
    print(f"[DEBUG] 模型原文: {text[:200]}")        # 看看模型到底回了啥

    # 从文本里抠出 JSON 数组:容忍 ```包裹、前后废话
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        courses = json.loads(text)                 # 解析成 Python list
    except json.JSONDecodeError:
        return {"error": "模型返回的不是合法 JSON", "raw": text}  # 兜底,别让接口 500
    return courses                                 # 返回数组,FastAPI 自动转成 JSON


# ============================================================
#  RAG MVP:上传讲义 → 切块 → 向量库 → 单轮问答
# ============================================================

def chunk(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """把长文本切成带重叠的块。
    size: 每块大概多少字符;overlap: 相邻块重叠多少字符,防止一句话被切断
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))   # 别越界,len(text) 是天花板
        chunks.append(text[start:end])
        if end == len(text):                 # 已经切到末尾,停
            break
        start += size - overlap              # 前进 size,但回退 overlap 做缓冲
    return chunks


@app.post("/api/rag/ingest")
async def ingest(file: UploadFile = File(...)):
    """上传一篇讲义(先支持 .txt):读文本 → 切块 → 存进向量库。"""
    raw = await file.read()
    text = raw.decode("utf-8", errors="ignore")   # 先按 txt 读;pdf 以后换这一行
    text = text.strip()
    if not text:
        return {"error": "文档内容为空"}

    chunks = chunk(text)
    # 每个 chunk 一个唯一 id;用文件名+序号,同名文件重传会覆盖(幂等)
    ids = [f"{file.filename}-{i}" for i in range(len(chunks))]

    # upsert:存在就更新、不存在就插入。比 add 更稳,重复上传不会报错
    collection.upsert(documents=chunks, ids=ids)
    return {"filename": file.filename, "chunks": len(chunks)}


class AskBody(BaseModel):
    question: str
    top_k: int = 3        # 检索几个最相关的片段


@app.post("/api/rag/ask")
async def ask(body: AskBody):
    """提问:向量库检索 top-k 片段 → 拼进 prompt → 交给 Claude 作答,并带回来源。"""
    # 1. 语义检索:Chroma 自动把问题向量化,返回最相近的几个片段
    results = collection.query(query_texts=[body.question], n_results=body.top_k)
    docs = results["documents"][0] if results["documents"] else []
    if not docs:
        return {"answer": "知识库里还没有相关内容,请先上传讲义。", "sources": []}

    # 2. 把检索到的片段拼成"资料"
    context = "\n\n".join(f"[片段{i + 1}]\n{d}" for i, d in enumerate(docs))

    # 3. 组装 prompt:限定只依据资料作答,防止幻觉
    prompt = f"""你是一个学习资料问答助手。请**只根据**下面提供的资料回答用户的问题。
如果资料里没有相关信息,就直接说"资料中未提及",不要编造。

=== 资料开始 ===
{context}
=== 资料结束 ===

用户问题:{body.question}

请用中文简洁作答。"""

    # 4. 调 Claude 生成答案
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = response.content[0].text

    # 5. 返回答案 + 命中的原始片段(溯源)
    return {"answer": answer, "sources": docs}
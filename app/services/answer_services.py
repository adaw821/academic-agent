import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

llm = ChatOpenAI(
    model = "glm-5.3",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key=os.environ["ZHIPU_API_KEY"],
)


def generate_answer(
        query: str,
        results: list[dict]
):
    if not results:
        return {
            "answer": "没有检索到任何有用资料",
            "retrieved_contexts": [],
            "sources": [],
        }

    #用来存放整理好的回答资料
    context_parts = []
    #从结果中取出父分片和文件名拼接
    for index, item in enumerate(results, start=1):
        context_parts.append(
            f"{index}文件:{item['filename']}"
            f"{item['parent_content']}"
        )

    #把context_parts中所有内容整理成字符串
    context = "\n\n".join(context_parts)

    messages = [
        SystemMessage(
            content="你是一个智能知识问答助手，必须严格基于客户提问，以及提供的参考资料回答问题，任何无法基于文件回答的一律说不知道"
        ),
        HumanMessage(
            content=f"问题:{query}, 参考资料:{context}"
        )
    ]

    response = llm.invoke(messages)

    return {
        "answer": response.content,
        "retrieved_contexts": [item["parent_content"] for item in results],
        "sources": [
            {
                "index": index,
                "document_id": item["document_id"],
                "parent_id": item["parent_id"],
                "filename": item["filename"],
            }
            for index, item in enumerate(results, start=1)
        ],
    }

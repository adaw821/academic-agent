import os

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.message import Message
from functools import lru_cache
from transformers import AutoTokenizer

# 辅助调用的应用预算，独立于最终回答的预算。
SUMMARY_CONTEXT_LIMIT = 65_536
SUMMARY_OUTPUT_TOKENS = 8_192
SUMMARY_SAFETY_MARGIN = 1_024

llm = ChatOpenAI(
    model = "glm-5.3",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key=os.environ["ZHIPU_API_KEY"],
)

@lru_cache(maxsize=1)
def get_tokenizer():
    return AutoTokenizer.from_pretrained("zai-org/GLM-5.3")


def count_message_tokens(messages: list[dict[str, str]]) -> int:
    tokenizer = get_tokenizer()

    token_ids = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
    )

    return len(token_ids)

def is_over_budget(
    input_tokens: int,
    context_limit: int,
    expected_output_tokens: int,
    safety_margin: int
) -> bool:
    """检查是否超预算"""
    available_input = (
        context_limit
        - expected_output_tokens
        - safety_margin
    )
    return input_tokens > available_input

def context_compress(
    history_context: str,
    conversation_id: int,
    db: Session,
    project_id: int,
    summarized_until_id: int,
    expected_summary_boundary: int | None,
):
    """压缩上下文"""
    system_prompt = (
        "你是一个ai历史记录总结助手，请将我提供的历史信息总结成summary，不许编造历史信息中没有的内容，首先要识别历史信息中的要点，做summary的时候必须提到每一个要点"
    )

    conversation = db.get(Conversation,conversation_id)
    if not conversation or conversation.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    previous_summary = conversation.summary
    if conversation.summarized_until_id != expected_summary_boundary or (
        expected_summary_boundary is not None
        and summarized_until_id <= expected_summary_boundary
    ):
        raise HTTPException(status_code=409, detail="会话摘要已变化，请重试本次提问。")
    summary = conversation.summary if conversation.summarized_until_id is not None else None
    if not summary:
        messages = [
        SystemMessage(
            content=system_prompt
        ),
        HumanMessage(
            content=(
                f"历史信息:\n\n{history_context}"
            )
        )
    ]

    else:
        messages = [
        SystemMessage(
            content=system_prompt
        ),
        HumanMessage(
            content=(
                 f"已有历史摘要:\n{summary}\n\n新增待总结历史:\n{history_context}"
            )
        )
    ]

    message_data = [
        {"role": "system", "content": messages[0].content},
        {"role": "user", "content": messages[1].content},
    ]
    if is_over_budget(
        count_message_tokens(message_data),
        SUMMARY_CONTEXT_LIMIT, SUMMARY_OUTPUT_TOKENS, SUMMARY_SAFETY_MARGIN,
    ):
        raise HTTPException(status_code=400, detail="待总结历史超过摘要模型的输入预算，历史摘要未更新。")

    response = llm.bind(max_tokens=SUMMARY_OUTPUT_TOKENS).invoke(messages)
    if response.response_metadata.get("finish_reason") == "length":
        raise HTTPException(status_code=502, detail="摘要生成达到输出上限，历史摘要未更新。")
    if not isinstance(response.content, str) or not response.content.strip():
        raise HTTPException(status_code=502, detail="摘要模型未返回有效文本，历史摘要未更新。")
    summary = response.content.strip()
    # 原子比较并更新，防止较慢的请求覆盖新摘要或让覆盖边界倒退。
    statement = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.project_id == project_id,
            Conversation.summarized_until_id == expected_summary_boundary,
            Conversation.summary == previous_summary,
        )
        .values(summary=summary, summarized_until_id=summarized_until_id)
        .execution_options(synchronize_session=False)
    )
    try:
        result = db.execute(statement)
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="会话摘要已被其他请求更新，请重试本次提问。")
        if not db.info.get("durable_workflow"):
            db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return summary

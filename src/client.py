# 封装langchain中的client接口，快速调用LLM模型
from langchain.chat_models import init_chat_model, BaseChatModel
import os
from dotenv import load_dotenv
load_dotenv()  # 加载环境变量


def get_chat_model(model_name: str, tools: list | None = None)->BaseChatModel:
    """获取聊天模型实例"""

    if "deepseek" in model_name.lower():
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = os.getenv("DEEPSEEK_API_URL")
        llm = init_chat_model(
            model_name,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url
        )
    else:
        llm = init_chat_model(model_name)
    
    if tools:
        llm = llm.bind_tools(tools)
    return llm
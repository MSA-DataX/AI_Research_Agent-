import os

from openai import OpenAI

from config import (
    LM_STUDIO_API_KEY,
    LM_STUDIO_BASE_URL,
    MODEL_NAME,
    REQUEST_TIMEOUT,
)

client = OpenAI(
    base_url=LM_STUDIO_BASE_URL,
    api_key=LM_STUDIO_API_KEY,
    timeout=REQUEST_TIMEOUT,
)


def _current_model() -> str:
    return os.getenv("MODEL_NAME") or MODEL_NAME


def chat(messages, temperature: float = 0.3) -> str:
    resp = client.chat.completions.create(
        model=_current_model(),
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def chat_with_tools(messages, tools, temperature: float = 0.2):
    return client.chat.completions.create(
        model=_current_model(),
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=temperature,
    )

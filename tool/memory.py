"""长期记忆摘要工具。

摘要失败时由调用方保留原有短期历史，不影响正常聊天。
"""

import json
from typing import Any

import requests

from tool.config import get_config


def summarize_history(history: list[dict[str, Any]], old_summary: str = "") -> str:
    config = get_config("./config.json")
    model_type = config.get("model_type", "deepseek")
    if model_type == "local":
        endpoint = config["local_api"]["qwen3_lora"]
        prompt = (
            "请把下面的桌宠聊天记录整理成简洁的长期记忆摘要。只保留用户明确表达的称呼、"
            "偏好、习惯、重要计划和已完成事项，不要臆测，不要记录敏感信息。"
            "使用中文，最多 500 字，直接输出摘要正文。\n"
            f"已有摘要：{old_summary or '无'}\n聊天记录：{json.dumps(history, ensure_ascii=False)}"
        )
        response = requests.post(endpoint, json={"history": [{"role": "user", "content": prompt}]}, timeout=120)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, str):
            return result.strip()
        return result.get("response", "").strip()

    endpoint = config["local_api"]["cloud_api"]
    model = "deepseek-chat" if model_type == "deepseek" else "qwen-plus"
    api_key = config.get("APIKEY", {}).get(model_type, "")
    prompt = (
        "请把下面的桌宠聊天记录整理成简洁的长期记忆摘要。只保留用户明确表达的称呼、"
        "偏好、习惯、重要计划和已完成事项，不要臆测，不要记录敏感信息。"
        "使用中文，最多 500 字，直接输出摘要正文。\n"
        f"已有摘要：{old_summary or '无'}\n聊天记录：{json.dumps(history, ensure_ascii=False)}"
    )
    payload = {
        "messages": [
            {"role": "system", "content": "你是一个严谨的个人记忆整理助手。"},
            {"role": "user", "content": prompt},
        ],
        "model": model,
        "max_tokens": 800,
        "stream": False,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    }
    response = requests.post(endpoint, json={"payload": payload, "headers": headers}, timeout=120)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"].strip()

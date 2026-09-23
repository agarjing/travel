import requests

"""
LLM 服务。

直连 Ollama 的 HTTP API，不走 langchain。
（终端里 ollama 可用，但 langchain 调用会报错，因此保留 HTTP 直连。）
"""


class LLMService:

    def __init__(self):
        self.url = "http://localhost:11434/api/generate"
        self.model = "deepseek-r1:1.5b"

    def generate(self, prompt: str) -> str:
        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )

        response.raise_for_status()

        data = response.json()

        return data["response"]

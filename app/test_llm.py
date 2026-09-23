from app.services.llm import LLMService


llm = LLMService()

result = llm.generate(
    "你好，请告诉我杭州有哪些著名的自然景点。"
)

print(result)
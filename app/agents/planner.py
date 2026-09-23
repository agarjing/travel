import json

from app.agents.state import AgentState, Observation
from app.models.travel import TravelRequest
from app.services.llm import LLMService
from app.tools.registry import ToolRegistry

"""
Planner 只负责"想"：构造 prompt、调用 LLM、把返回解析成决策。
它不执行任何工具，也不修改状态。

Prompt 设计说明：
deepseek-r1:1.5b 是个 1.8B 的小模型，实测对长 prompt 和多份示例
非常敏感 —— 规则、示例一多，它就会自创 schema 并编造字段。
所以这里的 prompt 刻意保持短、命令式，并且按当前状态只展示
"这一轮真正允许的动作"。
"""

# deepseek-r1 的推理块结束标签。
# 用拼接而不是字面量，避免字面量在传递过程中被意外清空。
THINK_CLOSE = "<" + "/" + "think" + ">"


class Planner:

    def __init__(
        self,
        llm_service: LLMService,
        tool_registry: ToolRegistry
    ):
        self.llm_service = llm_service
        self.tool_registry = tool_registry

    # =========================================================
    # LLM 调用
    # =========================================================

    def decide(self, prompt: str) -> dict:

        llm_result = self.llm_service.generate(prompt)

        print("LLM:")
        print(llm_result)

        decision = self.parse_llm_result(llm_result)

        print("Decision:")
        print(decision)

        return decision

    # =========================================================
    # JSON 解析
    # =========================================================

    def parse_llm_result(self, llm_result: str) -> dict:
        """
        解析 LLM 返回的 JSON。

        兼容：
        1. 纯 JSON
        2. ```json ... ```
        3. JSON 前面存在其他文字
        4. LLM 一次返回多个 JSON，只解析第一个完整 JSON
        5. deepseek-r1 的推理块（思考内容里可能含 { ）
        """

        text = llm_result.strip()

        # deepseek-r1 会先输出推理过程。推理文本里可能出现 { }，
        # 如果直接找第一个 { 会从推理块里截到非法 JSON，必须先剥离。
        if THINK_CLOSE in text:
            text = text.split(THINK_CLOSE)[-1]

        text = text.strip()

        # 去除 Markdown 代码块
        if text.startswith("```json"):
            text = text[7:]

        elif text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()

        # 找到第一个 JSON 对象
        start = text.find("{")

        if start == -1:
            raise ValueError(
                f"LLM 没有返回 JSON：\n{llm_result}"
            )

        decoder = json.JSONDecoder()

        try:
            result, _ = decoder.raw_decode(
                text[start:]
            )

        except json.JSONDecodeError as e:

            raise ValueError(
                f"LLM 返回内容无法解析为合法 JSON：\n"
                f"{llm_result}"
            ) from e

        if not isinstance(result, dict):
            raise ValueError(
                f"LLM 返回的 JSON 不是对象：\n{llm_result}"
            )

        return result

    # =========================================================
    # 合法动作列表（从注册表动态生成）
    # =========================================================

    def legal_actions(self) -> str:

        tool_names = "、".join(
            self.tool_registry.get_tool_names()
        )

        return (
            f"tool_call（tool 只能是：{tool_names}）\n"
            f"final"
        )

    # =========================================================
    # Prompt 构造
    #
    # 每轮都从初始 prompt 重新构造，而不是在上一轮的 prompt 后面
    # 不断追加。这样 POI / 路线 JSON 只会出现一次，prompt 长度稳定，
    # 对小模型更友好。
    # =========================================================

    def prompt_for(
        self,
        state: AgentState,
        request: TravelRequest,
        observation: Observation
    ) -> str:

        prompt = self.initial_prompt(request, state)

        if observation.kind == "tool_result":
            return prompt + self._tool_result_block(observation)

        if observation.kind == "tool_error":
            return prompt + self._tool_error_block(observation)

        if observation.kind == "json_error":
            return prompt + self._json_error_block(observation)

        if observation.kind == "validation_error":
            return prompt + self._validation_error_block(observation)

        if observation.kind == "duplicate_call":
            return prompt + self._duplicate_call_block(observation)

        if observation.kind == "unknown_decision":
            return prompt + self._unknown_decision_block(observation)

        if observation.kind == "llm_error":
            return prompt + self._llm_error_block(observation)

        return prompt

    def initial_prompt(
        self,
        request: TravelRequest,
        state: AgentState
    ) -> str:

        has_pois = bool(state.pois)

        return f"""
你是一名旅行规划 Agent。

用户需求：
目的地：{request.destination}
旅行天数：{request.days}
用户偏好：{request.preferences}
开始时间：{request.start_time}

可用 Tool：
{self.tool_registry.get_tool_descriptions()}

========================
已经获取的 POI
========================

{state.pois_json()}

========================
已经获取的路线
========================

{state.routes_json()}

========================
本轮怎么走
========================

{self._stage_directive(has_pois)}

========================
输出格式
========================

{self._format_hint(request, state, has_pois)}

规则：
1. poi_id 只能取自上面"已经获取的 POI"，不允许编造景点名称或 poi_id。
2. plan 必须包含 {request.days} 天，每天至少一个 item。
3. 每个 item 必须同时有 time 和 poi_id。
4. 每次只输出一个 JSON，不要输出 Markdown，不要输出 ```json，不要输出解释文字。
"""

    def _stage_directive(self, has_pois: bool) -> str:

        if not has_pois:

            return (
                "你现在还没有获取任何 POI。\n"
                "本轮不允许返回 final —— 你还不知道有哪些景点，"
                "凭空写出的 poi_id 一定不合法。\n"
                "本轮唯一允许的动作是调用 search_poi。"
            )

        return (
            "你的任务是：为每一天挑选合适的景点，然后返回 final。\n"
            "景点之间的路线不需要你手动计算 —— 你返回 final 之后，"
            "系统会自动补齐每天相邻景点之间的路线。\n"
            "（如果你确实想先查看某两个景点之间的路线，也可以调用 "
            "calculate_route，但不要求你这么做。）"
        )

    def _format_hint(
        self,
        request: TravelRequest,
        state: AgentState,
        has_pois: bool
    ) -> str:

        if not has_pois:

            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "search_poi",
                    "arguments": {
                        "destination": request.destination,
                        "keyword": ""
                    }
                },
                ensure_ascii=False,
                indent=4
            ) + "\n\n只输出上面这一个 JSON。"

        return f"""
返回 final（示例里的 poi_id 都真实存在，可以照这个结构填写）：

{self._final_example(request, state)}

只输出一个 JSON。
"""

    def _final_example(
        self,
        request: TravelRequest,
        state: AgentState
    ) -> str:
        """
        用 state 里真实存在的 poi_id 拼一个 final 示例。

        示例本身必须是合法的：如果示例里写了不存在的 poi_id，
        小模型会照着抄，反而制造出校验失败。
        """

        poi_ids = state.poi_ids()

        plan = []
        cursor = 0

        for day in range(1, request.days + 1):

            items = []

            for time in ("09:00", "14:00"):

                if not poi_ids:
                    break

                items.append(
                    {
                        "time": time,
                        "poi_id": poi_ids[cursor % len(poi_ids)]
                    }
                )

                cursor += 1

            plan.append({"day": day, "items": items})

        return json.dumps(
            {
                "type": "final",
                "answer": f"{request.destination}旅行规划。",
                "plan": plan
            },
            ensure_ascii=False,
            indent=4
        )

    # =========================================================
    # 各类 Observation 对应的提示块
    #
    # 初始 prompt 已经包含最新的 state，这里只补"上一轮出了什么问题"。
    # =========================================================

    def _tool_result_block(self, observation: Observation) -> str:

        return f"""

========================
Tool 执行结果
========================

Tool：{observation.tool_name}
参数：{json.dumps(observation.arguments, ensure_ascii=False)}
返回：{self._serialize(observation.data)}

上面的 POI / 路线已经更新到本次状态中，请继续决策下一步。
只输出一个 JSON。
"""

    def _tool_error_block(self, observation: Observation) -> str:

        return f"""

========================
Tool 执行失败
========================

Tool：{observation.tool_name}
参数：{json.dumps(observation.arguments, ensure_ascii=False)}
错误：{observation.error}

请换一个合法动作重新决策。

注意：景点名称不是 Tool，tool 字段只能填 {self._tool_names()}。
只输出一个 JSON。
"""

    def _json_error_block(self, observation: Observation) -> str:

        return f"""

========================
JSON 格式错误
========================

你上一次的返回无法解析为 JSON：

{observation.error}

请重新输出一个**合法**的 JSON。

常见错误：多个键之间缺少逗号；字符串没有用双引号包住；
在 JSON 之外输出了解释文字。

只输出一个 JSON。
"""

    def _validation_error_block(self, observation: Observation) -> str:

        data = observation.data or {}

        return f"""

========================
计划校验失败
========================

你上一次返回的 final 没有通过校验：

{observation.error}

你上一次返回的 plan：

{json.dumps(data.get("your_plan"), ensure_ascii=False, indent=2)}

请修正后重新返回一个完整的 final。

注意：poi_id 必须严格取自上面"已经获取的 POI"里列出的 id，
不要用景点名称，也不要编造。
只输出一个 JSON。
"""

    def _duplicate_call_block(self, observation: Observation) -> str:

        return f"""

========================
重复的 Tool 调用
========================

你刚刚又一次调用了完全相同的 Tool，参数一个没变：

Tool：{observation.tool_name}
参数：{json.dumps(observation.arguments, ensure_ascii=False)}

结果已经在上面的状态里了，重复调用不会有新信息。

请换一个动作：要么调用参数不同的 Tool，要么直接返回 final。
只输出一个 JSON。
"""

    def _unknown_decision_block(self, observation: Observation) -> str:

        return f"""

========================
无法识别的动作
========================

你上一次返回的 type 不是合法动作：

{json.dumps(observation.data, ensure_ascii=False)}

type 只能是 "tool_call" 或 "final"。

只输出一个 JSON。
"""

    def _llm_error_block(self, observation: Observation) -> str:

        return f"""

========================
LLM 调用失败
========================

上一次调用没有拿到有效响应：

{observation.error}

请重新进行决策，只输出一个 JSON。
"""

    # =========================================================
    # 工具
    # =========================================================

    def _tool_names(self) -> str:

        return "、".join(
            self.tool_registry.get_tool_names()
        )

    def _serialize(self, value) -> str:

        if isinstance(value, list):

            return json.dumps(
                [
                    item.model_dump()
                    for item in value
                ],
                ensure_ascii=False
            )

        return json.dumps(
            value.model_dump(),
            ensure_ascii=False
        )

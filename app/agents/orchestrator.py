import json
import time

from app.agents.executor import Executor
from app.agents.planner import Planner
from app.agents.state import AgentState, Observation
from app.models.travel import TravelPlan, TravelRequest
from app.services.llm import LLMService
from app.tools.registry import ToolRegistry

"""
TravelOrchestrator 是 Planner -> Executor 循环的驱动器。

每一步都由 LLM 决定（tool_call 或 final），Python 不再预设任何动作。
所有预算都是具名常量，不再散落魔法数字。
"""


class TravelOrchestrator:

    # 单步 ReAct 的步数上限。最小成功路径 =
    # search_poi + N 次 calculate_route + final，很容易到 8-10 步
    MAX_STEPS = 12

    # 各类错误独立计数，连续出错时能远早于 MAX_STEPS 退出
    MAX_JSON_ERRORS = 3
    MAX_TOOL_ERRORS = 4
    MAX_VALIDATION_ERRORS = 3

    # 总墙钟预算。单次 LLM 调用自带 120s 超时，
    # 本地 1.5B 模型 12 步可能很久，必须有个总闸
    TIME_BUDGET_SECONDS = 300

    def __init__(self):
        # LLMService 无状态，可以跨请求复用
        self.llm_service = LLMService()

    def run(self, request: TravelRequest) -> TravelPlan:

        state = AgentState()

        # 按请求构造：calculate_route 需要读本次请求已获取的 POI
        tool_registry = ToolRegistry(state.pois)

        planner = Planner(self.llm_service, tool_registry)
        executor = Executor(tool_registry, state)

        prompt = planner.initial_prompt(request, state)

        deadline = time.monotonic() + self.TIME_BUDGET_SECONDS

        json_errors = 0
        tool_errors = 0
        validation_errors = 0

        # 记住最后一次 final，用于预算耗尽后的 best-effort 降级
        last_final = None

        # 上一次的 tool_call 签名，用于拦截完全重复的调用
        last_call_signature = None

        for step in range(self.MAX_STEPS):

            if time.monotonic() > deadline:

                print(
                    f"超过时间预算 "
                    f"{self.TIME_BUDGET_SECONDS}s，提前结束"
                )
                break

            print()
            print("=" * 50)
            print(f"Agent Step: {step + 1}")
            print("=" * 50)

            try:

                decision = planner.decide(prompt)

            except ValueError as e:

                # LLM 返回的内容无法解析成 JSON
                json_errors += 1

                print("JSON Parse Error：")
                print(e)

                observation = Observation(
                    kind="json_error",
                    error=str(e)
                )

            except Exception as e:

                # LLM 调用本身失败（网络、超时）
                tool_errors += 1

                print("LLM Error：")
                print(e)

                observation = Observation(
                    kind="llm_error",
                    error=str(e)
                )

            else:

                decision_type = decision.get("type")

                if decision_type == "final":

                    last_final = decision

                    observation = executor.finalize(
                        request,
                        decision
                    )

                    if observation.kind == "tool_result":
                        return observation.data

                    validation_errors += 1

                    print("Plan Validation Error：")
                    print(observation.error)

                elif decision_type == "tool_call":

                    arguments = decision.get("arguments") or {}

                    signature = (
                        decision.get("tool"),
                        json.dumps(
                            arguments,
                            sort_keys=True,
                            ensure_ascii=False
                        )
                    )

                    if signature == last_call_signature:

                        # 小模型经常原地打转：重复完全相同的调用，
                        # 状态不会变化，只会白白烧掉时间和预算
                        validation_errors += 1

                        print("Duplicate Tool Call：")
                        print(signature)

                        observation = Observation(
                            kind="duplicate_call",
                            tool_name=decision.get("tool"),
                            arguments=arguments
                        )

                    else:

                        last_call_signature = signature

                        observation = executor.run_tool(decision)

                        if observation.kind == "tool_error":

                            print("Tool Error：")
                            print(observation.error)

                        else:

                            print("Tool Result：")
                            print(observation.data)

                else:

                    validation_errors += 1

                    print("Unknown Decision：")
                    print(decision)

                    observation = Observation(
                        kind="unknown_decision",
                        data=decision
                    )

            # 每轮都从初始 prompt 重建，只带上这一轮的 observation
            prompt = planner.prompt_for(
                state,
                request,
                observation
            )

            if (json_errors >= self.MAX_JSON_ERRORS
                    or tool_errors >= self.MAX_TOOL_ERRORS
                    or validation_errors >= self.MAX_VALIDATION_ERRORS):

                print("错误预算耗尽，提前结束")
                break

        # =====================================================
        # 预算耗尽：能降级就降级，否则报错
        # =====================================================

        if last_final and last_final.get("plan"):

            print(
                "Agent 未在预算内完成旅行规划，"
                "返回 best-effort 部分计划"
            )

            return executor.best_effort_plan(
                request,
                last_final
            )

        raise RuntimeError(
            f"Agent 在 {self.MAX_STEPS} 步 / "
            f"{self.TIME_BUDGET_SECONDS}s 内"
            f"未能产出可用的旅行计划"
        )

from app.agents.state import AgentState, Observation
from app.models.travel import (
    DayPlan,
    ItineraryItem,
    TravelPlan,
    TravelRequest
)
from app.tools.registry import ToolRegistry

"""
Executor 只负责"做"：校验参数、执行工具、写入状态、组装最终计划。

它不抛异常给上层（除了兜底组装失败），所有失败都转成 Observation
回灌给 Planner 重新决策。
"""

# 兜底补齐路线时用的工具名，避免在代码里散落魔法字符串
ROUTE_TOOL = "calculate_route"


class Executor:

    def __init__(
        self,
        tool_registry: ToolRegistry,
        state: AgentState
    ):
        self.tool_registry = tool_registry
        self.state = state

    # =========================================================
    # 执行 tool_call
    # =========================================================

    def run_tool(self, decision: dict) -> Observation:

        tool_name = decision.get("tool")

        arguments = decision.get("arguments") or {}

        errors = self.validate_arguments(tool_name, arguments)

        if errors:

            return Observation(
                kind="tool_error",
                tool_name=tool_name,
                arguments=arguments,
                error="\n".join(errors)
            )

        try:

            tool_result = self.tool_registry.execute(
                tool_name,
                arguments
            )

        except Exception as e:

            return Observation(
                kind="tool_error",
                tool_name=tool_name,
                arguments=arguments,
                error=str(e)
            )

        self.state.record_tool_result(
            tool_result,
            arguments
        )

        return Observation(
            kind="tool_result",
            tool_name=tool_name,
            arguments=arguments,
            data=tool_result
        )

    # =========================================================
    # 参数校验
    # =========================================================

    def validate_arguments(
        self,
        tool_name,
        arguments: dict
    ) -> list:
        """
        返回错误列表，空列表表示通过。

        这里承担过去散在 Agent 循环里的"防编造"职责：
        任何以 _id 结尾的参数都必须是已经获取到的 POI，
        不允许 LLM 凭空编造 poi_id。
        """

        errors = []

        if tool_name not in self.tool_registry.get_tool_names():

            errors.append(
                f"Tool 不存在：{tool_name}\n\n"
                f"当前可用 Tool：\n"
                f"{self.tool_registry.get_tool_descriptions()}\n\n"
                f"注意：景点名称不是 Tool。"
            )

            return errors

        for key, value in arguments.items():

            if not key.endswith("_id"):
                continue

            if self.state.get_poi(value) is None:

                available = "、".join(
                    self.state.poi_ids()
                ) or "（还没有获取到任何 POI）"

                errors.append(
                    f"找不到 {key}：{value}\n"
                    f"当前可用的 poi_id：{available}"
                )

        return errors

    # =========================================================
    # 处理 final
    # =========================================================

    def finalize(
        self,
        request: TravelRequest,
        decision: dict
    ) -> Observation:
        """
        校验并组装最终计划。

        成功时返回 kind="tool_result"、data=TravelPlan 的 Observation，
        失败时返回 kind="validation_error"，由 Planner 重试。
        """

        plan_data = decision.get("plan")

        errors = self.validate_plan(request, plan_data)

        if errors:

            return Observation(
                kind="validation_error",
                data={
                    "errors": errors,
                    "your_plan": plan_data,
                    "pois": self.state.pois_json()
                },
                error="\n".join(errors)
            )

        day_plans = self.build_day_plans(plan_data)

        self.ensure_routes(day_plans)

        return Observation(
            kind="tool_result",
            data=TravelPlan(
                destination=request.destination,
                days=request.days,
                answer=decision.get("answer", ""),
                plan=day_plans
            )
        )

    def validate_plan(
        self,
        request: TravelRequest,
        plan_data
    ) -> list:

        errors = []

        if not isinstance(plan_data, list) or not plan_data:
            return ["plan 不能为空，必须包含每一天的行程"]

        if len(plan_data) != request.days:

            errors.append(
                f"plan 必须包含 {request.days} 天，"
                f"当前只有 {len(plan_data)} 天"
            )

        for index, day_data in enumerate(plan_data):

            if not isinstance(day_data, dict):

                errors.append(
                    f"plan 第 {index + 1} 个元素不是对象"
                )
                continue

            day = day_data.get("day")

            if not isinstance(day, int) or isinstance(day, bool):

                errors.append(
                    f"plan 第 {index + 1} 个元素的 day "
                    f"必须是整数，当前是：{day!r}"
                )

            items = day_data.get("items")

            if not isinstance(items, list) or not items:

                errors.append(
                    f"day {day} 的 items 不能为空"
                )
                continue

            errors.extend(
                self.validate_items(day, items)
            )

        return errors

    def validate_items(
        self,
        day,
        items: list
    ) -> list:

        errors = []

        for index, item_data in enumerate(items):

            if not isinstance(item_data, dict):

                errors.append(
                    f"day {day} 第 {index + 1} 个 item 不是对象"
                )
                continue

            if not item_data.get("time"):

                errors.append(
                    f"day {day} 第 {index + 1} 个 item 缺少 time"
                )

            poi_id = item_data.get("poi_id")

            if not poi_id:

                errors.append(
                    f"day {day} 第 {index + 1} 个 item 缺少 poi_id"
                )

            elif self.state.get_poi(poi_id) is None:

                errors.append(
                    f"day {day} 第 {index + 1} 个 item 的 "
                    f"poi_id 不存在：{poi_id}"
                )

        return errors

    # =========================================================
    # 组装
    # =========================================================

    def build_day_plans(self, plan_data: list) -> list:

        day_plans = []

        for day_data in plan_data:

            items = []

            for item_data in day_data["items"]:

                items.append(
                    ItineraryItem(
                        time=item_data["time"],
                        # poi_id 一定来自 state.pois，
                        # 坐标、地址永远不采信 LLM 的输入
                        poi=self.state.get_poi(item_data["poi_id"])
                    )
                )

            day_plans.append(
                DayPlan(
                    day=day_data["day"],
                    items=items
                )
            )

        return day_plans

    def ensure_routes(self, day_plans: list) -> None:
        """
        兜底：保证每天相邻景点之间都有路线。

        LLM 已经算过的腿直接复用，绝不重复计算或覆盖。
        """
        for day_plan in day_plans:

            items = day_plan.items

            for i in range(1, len(items)):

                previous = items[i - 1].poi
                current = items[i].poi

                route = self.state.get_route(
                    previous.id,
                    current.id
                )

                if route is None:

                    try:

                        route = self.tool_registry.execute(
                            ROUTE_TOOL,
                            {
                                "origin_id": previous.id,
                                "destination_id": current.id
                            }
                        )

                    except Exception as e:

                        print(
                            f"补齐路线失败："
                            f"{previous.id} -> {current.id}：{e}"
                        )
                        continue

                    self.state.record_tool_result(
                        route,
                        {
                            "origin_id": previous.id,
                            "destination_id": current.id
                        }
                    )

                items[i].route_from_previous = route

    # =========================================================
    # best-effort 组装
    # =========================================================

    def best_effort_plan(
        self,
        request: TravelRequest,
        decision: dict
    ) -> TravelPlan:
        """
        预算耗尽时的降级路径：只保留合法的 item，
        丢弃非法项，而不是直接报错。
        """

        plan_data = decision.get("plan") or []

        day_plans = []

        for day_data in plan_data:

            if not isinstance(day_data, dict):
                continue

            day = day_data.get("day")

            if not isinstance(day, int) or isinstance(day, bool):
                continue

            items = []

            for item_data in day_data.get("items") or []:

                if not isinstance(item_data, dict):
                    continue

                time = item_data.get("time")
                poi = self.state.get_poi(item_data.get("poi_id"))

                if not time or poi is None:
                    continue

                items.append(
                    ItineraryItem(time=time, poi=poi)
                )

            if items:
                day_plans.append(
                    DayPlan(day=day, items=items)
                )

        self.ensure_routes(day_plans)

        return TravelPlan(
            destination=request.destination,
            days=request.days,
            answer=decision.get("answer", ""),
            plan=day_plans
        )

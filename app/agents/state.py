import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models.travel import POI, Route

"""
Agent 运行期的共享状态。

AgentState 由 Orchestrator 创建并持有，只有 Executor 写入，
Planner 和 Tool 只读。

工具执行结果统一记录在这里，替代过去"局部变量 + 拼进 prompt"的做法。
"""


@dataclass
class Observation:
    """
    Executor 执行一轮动作后的结果，回灌给 Planner。

    kind 取值：
      tool_result      工具执行成功
      tool_error       工具不存在 / 参数非法 / 执行抛异常
      llm_error        LLM 调用失败（网络、超时）
      json_error       LLM 返回的内容无法解析为 JSON
      validation_error final 里的 plan 没通过校验
      duplicate_call   与上一轮完全相同的 tool_call，已被拦截
      unknown_decision LLM 返回了无法识别的动作类型
    """

    kind: str
    tool_name: Optional[str] = None
    arguments: dict = field(default_factory=dict)
    data: Any = None
    error: Optional[str] = None


class AgentState:

    def __init__(self):

        # poi_id -> POI。必须原地修改，绝不能整体重新赋值：
        # 同一个 dict 对象被注入给 RouteTool 用于解析 id，重新赋值会让工具拿到旧引用。
        self.pois: dict[str, POI] = {}

        self.routes: list[Route] = []

        # (origin_id, destination_id) -> Route，用于判断某条路线是否已经算过
        self.route_index: dict[tuple[str, str], Route] = {}

    # =========================================================
    # 写入
    # =========================================================

    def add_pois(self, pois: list) -> None:

        for poi in pois:

            if not isinstance(poi, POI):
                continue

            # 同一个 POI 可能被多次搜索返回，按 id 去重
            self.pois[poi.id] = poi

    def add_route(
        self,
        origin_id: str,
        destination_id: str,
        route: Route
    ) -> None:

        key = (origin_id, destination_id)

        if key in self.route_index:
            return

        self.route_index[key] = route
        self.routes.append(route)

    def record_tool_result(
        self,
        tool_result,
        arguments: dict
    ) -> None:
        """
        按工具返回值的运行时类型归档，而不是按工具名硬编码分支。
        新增工具只要返回 POI 列表或 Route，这里无需改动。
        """

        if isinstance(tool_result, list):

            self.add_pois(tool_result)

        elif isinstance(tool_result, Route):

            origin_id = arguments.get("origin_id")
            destination_id = arguments.get("destination_id")

            if origin_id and destination_id:
                self.add_route(
                    origin_id,
                    destination_id,
                    tool_result
                )

    # =========================================================
    # 读取
    # =========================================================

    def get_poi(self, poi_id: str):

        if not isinstance(poi_id, str):
            return None

        return self.pois.get(poi_id)

    def get_route(
        self,
        origin_id: str,
        destination_id: str
    ):

        return self.route_index.get(
            (origin_id, destination_id)
        )

    def poi_ids(self) -> list:

        return list(self.pois.keys())

    # =========================================================
    # 序列化（供 Planner 拼 prompt）
    # =========================================================

    def pois_json(self) -> str:

        return json.dumps(
            [
                poi.model_dump()
                for poi in self.pois.values()
            ],
            ensure_ascii=False,
            indent=2
        )

    def routes_json(self) -> str:

        return json.dumps(
            [
                route.model_dump()
                for route in self.routes
            ],
            ensure_ascii=False,
            indent=2
        )

from app.models.travel import POI
from app.tools.poi import POITool
from app.tools.route import RouteTool


class ToolRegistry:
    """
    工具注册表。

    现在按请求构造：calculate_route 需要读取本次请求已获取到的 POI，
    所以构造时必须把 AgentState.pois 这个 dict 传进来。
    工具本身无状态，重复构造开销可忽略。
    """

    def __init__(self, poi_store: dict[str, POI]):

        self.poi_tool = POITool()
        self.route_tool = RouteTool(poi_store)

        self.tools = {
            "search_poi": self.poi_tool.search_poi,
            "calculate_route": self.route_tool.calculate_route
        }

    def execute(self, tool_name: str, arguments: dict):

        if tool_name not in self.tools:
            raise ValueError(
                f"Unknown tool: {tool_name}"
            )

        if arguments is None:
            arguments = {}

        return self.tools[tool_name](**arguments)

    def get_tool_names(self) -> list:
        """
        供 Planner 动态生成"合法动作"列表，
        新增工具后无需再去改 prompt 文案。
        """

        return list(self.tools.keys())

    def get_tool_descriptions(self) -> str:

        return """
search_poi：
功能：搜索目的地的旅游景点。

参数：
destination：目的地
keyword：景点类型，可选值：自然景观、人文景观、商业街。不筛选时传空字符串

----------------

calculate_route：
功能：计算两个景点之间的路线。

参数：
origin_id：起点景点 ID
destination_id：终点景点 ID

注意：origin_id 和 destination_id 必须是 search_poi 已经返回过的 poi_id。
"""

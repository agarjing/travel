from app.models.travel import POI, Route
from app.services.map import MapService


class RouteTool:

    def __init__(self, poi_store: dict[str, POI]):
        """
        poi_store 与 AgentState.pois 是同一个 dict 对象。
        工具只读它，用于把 LLM 给出的 poi_id 解析成真实 POI，
        这样 LLM 的参数就能直接是 id，不需要 Agent 在中间做翻译。
        """

        self.poi_store = poi_store
        self.map_service = MapService()

    def calculate_route(
        self,
        origin_id: str,
        destination_id: str
    ) -> Route:

        origin = self.poi_store[origin_id]
        destination = self.poi_store[destination_id]

        return self.map_service.calculate_route(
            origin=origin,
            destination=destination
        )

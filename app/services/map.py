from app.models.travel import POI, Route


class MapService:

    def calculate_route(
        self,
        origin: POI,
        destination: POI
    ) -> Route:

        # 当前使用 Mock 数据
        return Route(
            origin=origin.name,
            destination=destination.name,
            distance_km=5.0,
            duration_minutes=20,
            transport_mode="driving"
        )
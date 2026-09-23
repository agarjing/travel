from typing import List

from app.models.travel import POI


class POIService:

    def search(
        self,
        destination: str,
        keyword: str
    ) -> List[POI]:

        pois = [
            POI(
                id="poi_001",
                name="西湖",
                category="自然景观",
                address="杭州市西湖区",
                latitude=30.25,
                longitude=120.15
            ),

            POI(
                id="poi_002",
                name="灵隐寺",
                category="人文景观",
                address="杭州市西湖区灵隐路",
                latitude=30.24,
                longitude=120.10
            ),

            POI(
                id="poi_003",
                name="西溪湿地",
                category="自然景观",
                address="杭州市西湖区天目山路",
                latitude=30.27,
                longitude=120.06
            ),

            POI(
                id="poi_004",
                name="河坊街",
                category="商业街",
                address="杭州市上城区",
                latitude=30.24,
                longitude=120.17
            )
        ]

        # 根据关键词过滤
        if keyword:

            result = [
                poi
                for poi in pois
                if keyword in poi.category
            ]

            if result:
                return result

        return pois
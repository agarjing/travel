from typing import List

from app.models.travel import POI
from app.services.poi import POIService


class POITool:

    def __init__(self):

        self.service = POIService()

    def search_poi(
        self,
        destination: str,
        keyword: str = ""
    ) -> List[POI]:

        return self.service.search(
            destination=destination,
            keyword=keyword
        )
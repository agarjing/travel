from typing import List

from pydantic import BaseModel, Field

"""
定义系统中数据格式
"""


class TravelRequest(BaseModel):
    """
    用户提交的旅行规划请求
    """

    destination: str = Field(
        min_length=1,
        description="目的地城市，例如：杭州"
    )

    days: int = Field(
        ge=1,
        le=7,
        description="旅行天数，1-7 天"
    )

    preferences: List[str] = Field(
        default_factory=list,
        description="用户偏好，例如：[\"自然\"]"
    )

    start_time: str = "09:00"


class POI(BaseModel):
    """
    一个兴趣点，例如景点、餐厅、酒店
    """

    id: str
    name: str
    category: str
    address: str
    latitude: float
    longitude: float


class Route(BaseModel):
    """
    两个 POI 之间的路线
    """

    origin: str
    destination: str
    distance_km: float
    duration_minutes: int
    transport_mode: str


class ItineraryItem(BaseModel):
    """
    一天中的一个行程
    """

    time: str
    poi: POI

    # 第一个景点没有前置路线
    route_from_previous: Route | None = None


class DayPlan(BaseModel):
    """
    一天的旅行计划
    """

    day: int
    items: List[ItineraryItem]


class TravelPlan(BaseModel):
    """
    最终完整的旅行计划
    """

    destination: str
    days: int
    answer: str = ""
    plan: List[DayPlan]
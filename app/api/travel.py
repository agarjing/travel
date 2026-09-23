from fastapi import APIRouter

from app.models.travel import (
    TravelRequest,
    TravelPlan
)

from app.agents.orchestrator import TravelOrchestrator


router = APIRouter(
    prefix="/api/travel",
    tags=["Travel"]
)


orchestrator = TravelOrchestrator()


@router.post(
    "/plan",
    response_model=TravelPlan
)
def create_travel_plan(
    request: TravelRequest
):

    return orchestrator.run(request)
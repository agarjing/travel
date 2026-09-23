from fastapi import FastAPI

from app.api.travel import router as travel_router


app = FastAPI(
    title="Travel Planning Agent",
    description="Travel Agent + Navigation Agent",
    version="1.0.0"
)


# 注册旅行相关 API
app.include_router(travel_router)


@app.get("/")
def root():

    return {
        "message": "Travel Agent is running"
    }
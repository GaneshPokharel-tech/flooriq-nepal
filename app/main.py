from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers.insights import router as insights_router
from app.routers.admin import router as admin_router
from app.routers.dashboard import router as dashboard_router

app = FastAPI(
    title="FloorIQ Nepal API",
    version="0.2.0",
    description="NEPSE floorsheet intelligence (analytics-only).",
)


@app.get("/health")
def health():
    return {"ok": True}


# Dashboard static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(dashboard_router)
app.include_router(insights_router)
app.include_router(admin_router)

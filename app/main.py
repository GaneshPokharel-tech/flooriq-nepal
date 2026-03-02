from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers.insights import router as insights_router
from app.routers.admin import router as admin_router
from app.routers.dashboard import router as dashboard_router

from app.middleware import BasicAuthMiddleware, RateLimitMiddleware
from app.dependencies import redis_client

app = FastAPI(
    title="FloorIQ Nepal API",
    version="0.2.0",
    description="NEPSE floorsheet intelligence (analytics-only).",
)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("startup")
async def _startup():
    # Optional Redis: attach to app.state for middleware use
    app.state.redis = await redis_client()


@app.on_event("shutdown")
async def _shutdown():
    r = getattr(app.state, "redis", None)
    try:
        if r is not None:
            await r.close()
    except Exception:
        pass


# Middleware (order matters: auth first, then rate limit)
app.add_middleware(BasicAuthMiddleware)
app.add_middleware(RateLimitMiddleware)

# Dashboard static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(dashboard_router)
app.include_router(insights_router)
app.include_router(admin_router)

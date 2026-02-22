from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/refresh")
def refresh():
    return {"ok": True, "message": "Placeholder. Use GitHub Actions or scripts for refresh for now."}

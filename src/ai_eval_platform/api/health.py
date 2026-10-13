from fastapi import APIRouter

from ai_eval_platform.storage.postgres import check_connection

router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness() -> dict[str, str]:
    if not check_connection():
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}

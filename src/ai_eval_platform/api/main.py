from fastapi import FastAPI

from ai_eval_platform.api.health import router as health_router
from ai_eval_platform.api.rca import router as rca_router
from ai_eval_platform.api.runs import router as runs_router

app = FastAPI(
    title="AI Evaluation Platform API",
    version="0.1.0",
    description=(
        "Agent-agnostic AI reliability API. Every persisted source event is "
        "projected into a normalized observation and a base signal; detector "
        "signals are additional findings, not prerequisites for signal creation."
    ),
)
app.include_router(health_router)
app.include_router(runs_router)
app.include_router(rca_router)
"""饮用水混配 API。

POST /plans        计算最低成本配比并保存方案（支持幂等键）
GET  /plans/{id}   按 ID 查询已保存的方案
GET  /health       健康检查
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import db
from .models import (AchievableIndicator, BlendRequest, InfeasibleResponse,
                     PlanResponse, UsageItem)
from .solver import INDICATORS, SourceData, solve


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()  # 启动时自动建库建表
    yield


app = FastAPI(
    title="饮用水混配 API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/plans",
    response_model=PlanResponse,
    status_code=201,
    responses={422: {"model": InfeasibleResponse,
                     "description": "约束不可行，返回指标可达范围"},
               409: {"description": "幂等键已被不同请求使用"}},
)
def create_plan(request: BlendRequest):
    canonical = json.loads(request.model_dump_json())

    if request.idempotency_key:
        existing = db.find_by_key(request.idempotency_key)
        if existing:
            stored = json.loads(existing["request_json"])
            if stored == json.loads(json.dumps(canonical, sort_keys=True)):
                return JSONResponse(status_code=200,
                                    content=json.loads(existing["response_json"]))
            raise HTTPException(
                status_code=409,
                detail="幂等键已被使用，且请求内容不一致",
            )

    targets = {
        name: (getattr(request.targets, name).min,
               getattr(request.targets, name).max)
        for name in INDICATORS
    }
    sources = [SourceData(id=s.id, calcium=s.calcium, magnesium=s.magnesium,
                          alkalinity=s.alkalinity, available=s.available,
                          cost_per_liter=s.cost_per_liter)
               for s in request.sources]
    result = solve(request.target_volume, targets, sources)

    if result.status == "infeasible":
        body = InfeasibleResponse(
            message="约束不可行：不存在满足全部指标区间与供给上限的配比",
            max_volume=result.achievable["max_volume"],
            evaluated_at_volume=result.achievable["evaluated_at_volume"],
            requested=request.targets,
            achievable={
                name: (AchievableIndicator(**r) if r else None)
                for name, r in result.achievable["indicators"].items()
            },
        )
        return JSONResponse(status_code=422,
                            content=json.loads(body.model_dump_json()))

    created_at = datetime.now(timezone.utc).isoformat()
    plan = PlanResponse(
        plan_id=uuid.uuid4().hex,
        target_volume=request.target_volume,
        total_cost=result.total_cost,
        usage=[UsageItem(source_id=sid, volume=vol)
               for sid, vol in result.usage.items()],
        created_at=created_at,
    )
    try:
        db.save_plan(plan.plan_id, request.idempotency_key, canonical,
                     plan.model_dump(mode="json"), created_at)
    except sqlite3.IntegrityError:
        # 并发下同一幂等键被抢先写入：返回已存在的方案
        existing = db.find_by_key(request.idempotency_key)
        if existing:
            return JSONResponse(status_code=200,
                                content=json.loads(existing["response_json"]))
        raise
    return JSONResponse(status_code=201,
                        content=json.loads(plan.model_dump_json()))


@app.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: str):
    row = db.find_by_id(plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="方案不存在")
    return json.loads(row["response_json"])

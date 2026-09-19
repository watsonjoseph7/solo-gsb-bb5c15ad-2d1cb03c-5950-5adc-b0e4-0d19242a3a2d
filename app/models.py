"""请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class IndicatorRange(BaseModel):
    """指标浓度区间，单位 mg/L（碱度以 CaCO3 计）。"""

    min: float = Field(ge=0)
    max: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> "IndicatorRange":
        if self.max < self.min:
            raise ValueError("max 必须大于等于 min")
        return self


class Targets(BaseModel):
    calcium: IndicatorRange
    magnesium: IndicatorRange
    alkalinity: IndicatorRange


class Source(BaseModel):
    """水源：浓度 mg/L，可用量 L，单位成本 元/L。"""

    id: str = Field(min_length=1, max_length=64)
    calcium: float = Field(ge=0)
    magnesium: float = Field(ge=0)
    alkalinity: float = Field(ge=0)
    available: float = Field(ge=0)
    cost_per_liter: float = Field(ge=0)


class BlendRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    target_volume: float = Field(gt=0, description="目标体积 L")
    targets: Targets
    sources: list[Source] = Field(min_length=2, max_length=6)

    @field_validator("sources")
    @classmethod
    def _unique_ids(cls, value: list[Source]) -> list[Source]:
        ids = [s.id for s in value]
        if len(ids) != len(set(ids)):
            raise ValueError("水源 id 必须唯一")
        return value


class UsageItem(BaseModel):
    source_id: str
    volume: float


class PlanResponse(BaseModel):
    plan_id: str
    status: str = "optimal"
    target_volume: float
    total_cost: float
    usage: list[UsageItem]
    created_at: str


class AchievableIndicator(BaseModel):
    min: float | None
    max: float | None


class InfeasibleResponse(BaseModel):
    status: str = "infeasible"
    message: str
    max_volume: float
    evaluated_at_volume: float
    requested: Targets
    achievable: dict[str, AchievableIndicator | None]

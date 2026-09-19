"""最低成本混配求解器。

基于 scipy.optimize.linprog (HiGHS) 求解线性规划：

    min  sum(cost_i * x_i)
    s.t. sum(x_i) = target_volume
         lo_j * V <= sum(conc_ij * x_i) <= hi_j * V   (j = 钙/镁/碱度)
         0 <= x_i <= available_i

成本并列时，按水源 ID 排序后逐步最小化各分量，
得到字典序最小的用量向量。不可行时计算各指标的可达浓度范围。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

INDICATORS = ("calcium", "magnesium", "alkalinity")

# 字典序优化时的成本约束松弛量（相对最优成本）。
# 取 0：HiGHS 求得的最优面精确满足该约束，可得到干净的顶点解；
# 若因数值误差导致某一步不可行，则保留当前解退出。
_COST_REL_TOL = 0.0


@dataclass
class SourceData:
    id: str
    calcium: float
    magnesium: float
    alkalinity: float
    available: float
    cost_per_liter: float


@dataclass
class SolveResult:
    status: str  # "optimal" | "infeasible"
    usage: dict[str, float] | None = None  # 按水源 ID 排序的用量
    total_cost: float | None = None
    achievable: dict | None = None  # 不可行时的可达范围


def _snap(x: np.ndarray, avail: np.ndarray, tol: float) -> np.ndarray:
    """把数值噪声吸附到 0 或供给上限上。"""
    x = np.asarray(x, dtype=float).copy()
    x[np.abs(x) < tol] = 0.0
    for i, a in enumerate(avail):
        if abs(x[i] - a) < tol:
            x[i] = float(a)
    return x


def _snap_value(v: float, upper: float, tol: float) -> float:
    if abs(v) < tol:
        return 0.0
    if abs(v - upper) < tol:
        return float(upper)
    return v


def solve(
    target_volume: float,
    targets: dict[str, tuple[float, float]],
    sources: list[SourceData],
) -> SolveResult:
    sources = sorted(sources, key=lambda s: s.id)
    n = len(sources)
    cost = np.array([s.cost_per_liter for s in sources])
    conc = np.array([[s.calcium, s.magnesium, s.alkalinity] for s in sources])
    avail = np.array([s.available for s in sources], dtype=float)

    a_eq = np.ones((1, n))
    b_eq = np.array([target_volume])

    ub_rows, ub_rhs = [], []
    for j, name in enumerate(INDICATORS):
        lo, hi = targets[name]
        col = conc[:, j]
        ub_rows.append(col)
        ub_rhs.append(hi * target_volume)
        ub_rows.append(-col)
        ub_rhs.append(-lo * target_volume)
    a_ub = np.array(ub_rows)
    b_ub = np.array(ub_rhs)
    bounds = [(0.0, float(a)) for a in avail]

    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                  bounds=bounds, method="highs")
    if res.status != 0:
        return SolveResult(status="infeasible",
                           achievable=_achievable(target_volume, conc, avail))

    # 字典序 tie-break：在成本不超过最优值的约束下，
    # 按水源 ID 顺序依次最小化 x_1, x_2, ..., x_n。
    optimal_cost = float(res.fun)
    cost_tol = _COST_REL_TOL * max(1.0, abs(optimal_cost))
    snap_tol = 1e-9 * max(1.0, target_volume)
    a_ub2 = np.vstack([a_ub, cost])
    b_ub2 = np.append(b_ub, optimal_cost + cost_tol)

    x = _snap(res.x, avail, snap_tol)
    fixed = list(bounds)
    for k in range(n):
        e_k = np.zeros(n)
        e_k[k] = 1.0
        step = linprog(e_k, A_ub=a_ub2, b_ub=b_ub2, A_eq=a_eq, b_eq=b_eq,
                       bounds=fixed, method="highs")
        if step.status != 0:
            break  # 数值异常时保留当前最优解
        v = _snap_value(float(np.clip(step.x[k], 0.0, avail[k])),
                        float(avail[k]), snap_tol)
        fixed[k] = (v, v)
        x = _snap(step.x, avail, snap_tol)

    usage = {sources[i].id: float(x[i]) for i in range(n)}
    return SolveResult(status="optimal", usage=usage,
                       total_cost=float(cost @ x))


def _achievable(target_volume: float, conc: np.ndarray,
                avail: np.ndarray) -> dict:
    """仅保留总量与供给上限约束，计算各指标可达浓度范围。"""
    total_available = float(avail.sum())
    volume = min(target_volume, total_available)
    result = {
        "max_volume": total_available,
        "evaluated_at_volume": volume,
        "indicators": {},
    }
    bounds = [(0.0, float(a)) for a in avail]
    a_eq = np.ones((1, len(avail)))
    for j, name in enumerate(INDICATORS):
        if volume <= 0:
            result["indicators"][name] = None
            continue
        col = conc[:, j]
        r_min = linprog(col, A_eq=a_eq, b_eq=[volume], bounds=bounds,
                        method="highs")
        r_max = linprog(-col, A_eq=a_eq, b_eq=[volume], bounds=bounds,
                        method="highs")
        lo = float(r_min.fun / volume) if r_min.status == 0 else None
        hi = float(-r_max.fun / volume) if r_max.status == 0 else None
        result["indicators"][name] = {"min": lo, "max": hi}
    return result

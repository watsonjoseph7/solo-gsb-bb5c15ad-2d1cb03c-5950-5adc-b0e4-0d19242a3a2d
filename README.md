# 饮用水混配 API

基于 **Python + FastAPI + SciPy + SQLite** 的饮用水混配优化服务：给定目标体积、
钙 / 镁 / 碱度浓度区间以及 2～6 种水源（浓度、可用量、单位成本），求解满足全部
指标区间与供给上限的**最低成本配比**；成本并列时取按水源 ID 排序后用量向量
**字典序最小**的方案；约束不可行时返回各指标的**可达浓度范围**。支持以幂等键
保存方案并按 ID 查询。

## 快速开始（单条命令：安装 + 建库 + 启动）

```bash
docker compose up --build
```

该命令完成镜像构建（依赖安装）、SQLite 建库建表（服务启动时自动执行）并启动服务。

- 服务地址：**http://localhost:8000**
- 交互式文档：http://localhost:8000/docs （Swagger UI）
- 健康检查：`curl http://localhost:8000/health`

## 配置

通过环境变量配置（`docker-compose.yml` 中已给出默认值）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8000` | 监听端口（容器内） |
| `DATABASE_PATH` | `/app/data/blends.db` | SQLite 数据库文件路径 |

宿主机端口映射在 `docker-compose.yml` 的 `ports` 中调整（默认 `8000:8000`）；
数据库文件通过命名卷 `blends-data` 持久化。

## 接口

### `POST /plans` — 计算并保存配比方案

请求体（浓度单位 mg/L，碱度以 CaCO₃ 计；体积 L；成本 元/L）：

```json
{
  "idempotency_key": "order-20260919-001",
  "target_volume": 1000,
  "targets": {
    "calcium":   {"min": 50, "max": 60},
    "magnesium": {"min": 5,  "max": 25},
    "alkalinity":{"min": 80, "max": 200}
  },
  "sources": [
    {"id": "spring-a", "calcium": 30, "magnesium": 8,  "alkalinity": 90,
     "available": 900, "cost_per_liter": 0.5},
    {"id": "well-b",   "calcium": 70, "magnesium": 20, "alkalinity": 180,
     "available": 600, "cost_per_liter": 0.8}
  ]
}
```

```bash
curl -X POST http://localhost:8000/plans \
  -H 'Content-Type: application/json' -d @request.json
```

- **201 Created**：求解成功，返回 `plan_id`、`total_cost` 与按水源 ID 排序的 `usage`。
- **200 OK**：`idempotency_key` 命中且请求内容一致，返回已保存的方案（幂等重放）。
- **409 Conflict**：幂等键已被不同内容的请求使用。
- **422 Unprocessable Entity**：约束不可行，返回各指标在目标体积下的可达范围
  （`achievable`）、最大可供体积（`max_volume`）及本次请求的区间（`requested`）。

`idempotency_key` 可省略，此时每次请求生成新方案。

### `GET /plans/{plan_id}` — 按 ID 查询方案

```bash
curl http://localhost:8000/plans/<plan_id>
```

未找到返回 404。

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://127.0.0.1:8000
pytest                                 # 运行测试
```

## 求解方法

以各水源取用量为变量建立线性规划（SciPy HiGHS）：目标为总成本最小，约束为
总体积守恒、三项指标总量区间、各水源供给上限。求得最优成本后，在“成本不超过
最优值”的约束下按水源 ID 顺序逐次最小化各水源用量，得到字典序最小的用量向量。
不可行时，仅保留体积与供给约束，对各指标分别求浓度上下界作为可达范围。

# Travel Agent 架构说明

一个基于 FastAPI + 本地 LLM（Ollama / deepseek-r1:1.5b）的旅行规划 Agent。
用户提交目的地、天数、偏好，系统返回一份结构化的逐日行程，并自动补全
每天相邻景点之间的路线。

核心设计：**Planner → Executor → Tools** 三层，每一步动作都由 LLM 决定，
Python 不预设任何工具调用。

## 目录结构与文件职责

```
Travel/
├── CLAUDE.md                     给 Claude Code 的项目约定
├── ARCHITECTURE.md               本文件
└── app/
    ├── main.py                   FastAPI 应用入口，注册路由，提供 / 和 /docs
    ├── api/
    │   └── travel.py             HTTP 接口层：POST /api/travel/plan
    ├── models/
    │   └── travel.py             Pydantic 数据模型（请求/响应/领域对象）
    ├── agents/                   Agent 编排层
    │   ├── orchestrator.py       循环驱动器：持有预算，跑 Planner→Executor
    │   ├── planner.py            只决策：构造 prompt、调 LLM、解析 JSON
    │   ├── executor.py           只执行：校验参数、调工具、写状态、组装结果
    │   └── state.py              运行期共享状态（AgentState / Observation）
    ├── tools/                    工具层（Agent 的"手"）
    │   ├── registry.py           工具注册表，按请求构造并注入 POI store
    │   ├── poi.py                search_poi：搜索景点
    │   └── route.py              calculate_route：按 poi_id 计算两点路线
    ├── services/                 外部依赖封装（数据源、LLM、地图）
    │   ├── llm.py                调用 Ollama HTTP API
    │   ├── poi.py                POI 数据源（当前为 mock 数据）
    │   └── map.py                路线计算（当前为 mock 数据）
    └── test_llm.py               调试脚本：单独验证 LLM 连通性
```

各层依赖方向单向：`api → agents → tools → services → models`。

## 一次请求的完整调用链

```
POST /api/travel/plan
  └─ TravelOrchestrator.run(request)
       ├─ AgentState()                    创建共享状态
       ├─ ToolRegistry(state.pois)        注入 POI store
       ├─ Planner(llm, registry)
       └─ Executor(registry, state)
            │
            └─ ReAct 循环（最多 MAX_STEPS 轮）
                 │
                 ├─ Planner.decide(prompt)
                 │    └─ LLMService.generate() → Ollama
                 │    └─ parse_llm_result() 解析成 decision
                 │
                 ├─ decision.type == "tool_call"
                 │    └─ Executor.run_tool()
                 │         ├─ validate_arguments()   poi_id 白名单校验
                 │         ├─ ToolRegistry.execute() 真正调用工具
                 │         └─ AgentState.record_tool_result()  写入状态
                 │
                 ├─ decision.type == "final"
                 │    └─ Executor.finalize()
                 │         ├─ validate_plan()   收集全部校验错误
                 │         ├─ build_day_plans() poi_id → 真实 POI
                 │         └─ ensure_routes()   兜底补齐相邻路线
                 │
                 └─ Planner.prompt_for(state, request, observation)
                      每轮都从 state 重建 prompt，只附加本轮的结果
```

## 三个关键设计

### 1. 每一步都由 LLM 决定，没有任何强制调用

旧实现在 Python 里硬编码了两步：先强制调 `search_poi`，再强制算
`pois[0] → pois[1]` 的路线，然后才把控制权交给 LLM。现在这些全部删除。

代价是 prompt 必须自己承担"引导模型先取数据"的职责，因此
`planner.initial_prompt` 会按当前 state 只展示**本轮真正允许的动作**：

- 还没有 POI 时 → 明确写"本轮不允许返回 final，唯一允许的动作是调用 search_poi"
- 已有 POI 时 → 告知可以返回 final，并说明路线会被自动补齐

这不属于"强制"：是否调用工具、调哪个、参数是什么，仍然由模型输出。

### 2. 状态集中在 AgentState，且只有一个写入方

`AgentState` 由 Orchestrator 创建，**只有 Executor 写入**，Planner 和 Tool 只读。

- `state.pois` 是 `dict[poi_id, POI]`，以引用方式注入 `ToolRegistry`，
  `RouteTool` 用它把 LLM 给的 `poi_id` 解析成真实 POI
- `state.routes` + `route_index` 以 `(origin_id, destination_id)` 为键，
  用于判断某条路线是否已经算过

注意事项：`state.pois` 必须**原地修改，绝不能整体重新赋值**
（不要写 `state.pois = {...}`），否则工具持有的是旧引用。

### 3. 所有失败都是可重试的 Observation，不抛异常

Executor 的 `run_tool` 和 `finalize` 都不向上抛异常，而是返回一个
`Observation` 让 Planner 重新决策：

| Observation kind   | 触发条件                                | Planner 的应对              |
|--------------------|-----------------------------------------|-----------------------------|
| `tool_result`      | 工具执行成功                            | 继续下一步                  |
| `tool_error`       | 工具不存在 / 参数非法 / 执行抛异常       | 换合法动作重试              |
| `json_error`       | LLM 返回内容无法解析为 JSON             | 重新输出合法 JSON           |
| `validation_error` | final 的 plan 没通过校验                | 按错误明细修正后重交        |
| `duplicate_call`   | 与上一轮完全相同的 tool_call（已拦截）  | 换动作或返回 final          |
| `unknown_decision` | type 不是 tool_call / final             | 重新决策                    |
| `llm_error`        | LLM 调用失败（网络、超时）              | 重试                        |

**防编造**：`Executor.validate_arguments` 会对任何以 `_id` 结尾的参数做
白名单校验，值必须存在于 `state.pois`。POI 的坐标和地址永远从
`state.pois` 取，从不采信 LLM 的输出。

## 预算

| 常量                    | 值   | 说明                                   |
|-------------------------|------|----------------------------------------|
| `MAX_STEPS`             | 12   | 单步 ReAct 的循环上限                  |
| `MAX_JSON_ERRORS`       | 3    | JSON 解析连续失败上限                  |
| `MAX_TOOL_ERRORS`       | 4    | 工具错误 + LLM 调用错误上限            |
| `MAX_VALIDATION_ERRORS` | 3    | 计划校验失败 + 重复调用上限            |
| `TIME_BUDGET_SECONDS`   | 300  | 总墙钟预算（单次 LLM 调用自带 120s 超时）|

预算耗尽时走 **best-effort**：如果最后一次 `final` 至少含一个合法 item，
就丢弃非法项返回部分计划；否则抛 `RuntimeError`（HTTP 500）。

## Swagger 运行案例

启动服务后打开 <http://127.0.0.1:8001/docs>：

```bash
# 用项目实际使用的 conda 环境启动
/Users/agar/opt/anaconda3/envs/travel-agent/bin/python3 -m uvicorn app.main:app --port 8001
```

在 Swagger UI 里展开 `POST /api/travel/plan` → Try it out → 填入请求体：

```json
{
  "destination": "杭州",
  "days": 2,
  "preferences": ["自然"],
  "start_time": "09:00"
}
```

等价的 curl：

```bash
curl -X POST http://127.0.0.1:8001/api/travel/plan \
  -H 'Content-Type: application/json' \
  -d '{"destination":"杭州","days":2,"preferences":["自然"],"start_time":"09:00"}'
```

### 实测返回（HTTP 200，耗时约 63s）

```json
{
  "destination": "杭州",
  "days": 2,
  "answer": "杭州旅行规划。",
  "plan": [
    {
      "day": 1,
      "items": [
        {
          "time": "09:00",
          "poi": {
            "id": "poi_001",
            "name": "西湖",
            "category": "自然景观",
            "address": "杭州市西湖区",
            "latitude": 30.25,
            "longitude": 120.15
          },
          "route_from_previous": null
        },
        {
          "time": "14:00",
          "poi": {
            "id": "poi_003",
            "name": "西溪湿地",
            "category": "自然景观",
            "address": "杭州市西湖区天目山路",
            "latitude": 30.27,
            "longitude": 120.06
          },
          "route_from_previous": {
            "origin": "西湖",
            "destination": "西溪湿地",
            "distance_km": 5.0,
            "duration_minutes": 20,
            "transport_mode": "driving"
          }
        }
      ]
    }
  ]
}
```

说明：

- 每天第一个 item 的 `route_from_previous` 为 `null`（没有前置路线）
- 结论性字段 `answer` 来自 LLM 的 `final.answer`，是给用户看的自然语言说明
- `poi` 里的坐标、地址来自 `services/poi.py` 的 mock 数据，不是模型编造的
- 第二天结构与第一天相同（`day: 2`，时间 09:00 / 14:00）
- 上面为节省篇幅只保留了 day 1 的完整内容，实际响应含完整的 2 天

### 这次运行实际发生的过程

Agent 循环跑了 3 步，可以在服务日志里看到完整轨迹：

```
Agent Step: 1   LLM 决定 tool_call  search_poi(destination=杭州, keyword="")
                → 返回 poi_001 西湖、poi_003 西溪湿地
Agent Step: 2   LLM 决定 final，但 plan 里把 poi_001 写成了 poi_0_01
                → 校验失败：day 2 第 1 个 item 的 poi_id 不存在：poi_0_01
                  day 2 第 2 个 item 的 poi_id 不存在：poi_0_03
Agent Step: 3   LLM 按错误明细修正后重新返回 final
                → 校验通过，Executor.ensure_routes 补齐路线，返回 200
```

第 2 步的失败是这套设计的典型体现：旧实现在这里是直接 `raise ValueError`
（HTTP 500），现在变成一条可重试的 observation，模型自己纠正了。

## 已知限制

- **POI 与路线都是 mock 数据**：`services/poi.py` 写死了 4 个杭州景点且
  `destination` 参数不参与过滤；`services/map.py` 对任意两点都返回
  5.0 km / 20 min。接真实数据源时只需替换这两个 service。
- **路线兜底是确定性逻辑**：`Executor.ensure_routes` 会用 `ROUTE_TOOL`
  补齐缺失路线。它不覆盖 LLM 已经算过的腿，但这仍是全流程唯一一处
  由 Python 指定工具名的地方，是刻意的设计取舍。
- **模型能力是主要瓶颈**：deepseek-r1:1.5b 实际是 1.8B 的小模型，实测
  对长 prompt 敏感，会自创字段、把 `poi_001` 写成 `poi_0_01`。因此
  `planner.py` 的 prompt 刻意保持短、命令式，并按状态只展示本轮允许的动作。
  若要提升稳定性，优先考虑换更大的模型（如 qwen2.5:7b）。
- **单次请求耗时较长**：本地小模型每次推理 20-60s，一次规划通常 40-70s。

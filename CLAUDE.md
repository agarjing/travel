# CLAUDE.md

旅行规划 Agent：FastAPI + 本地 Ollama LLM，输入目的地/天数/偏好，输出逐日行程。

目录结构、调用链、Swagger 实测案例见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 运行

项目实际使用的环境是 conda env `travel-agent`（Python 3.12），
仓库里的 `.venv` 是空的 Python 3.9 壳子，不要用它。

```bash
# 依赖 Ollama 在本地跑着，模型 deepseek-r1:1.5b
curl -s localhost:11434/api/tags

/Users/agar/opt/anaconda3/envs/travel-agent/bin/python3 -m uvicorn app.main:app --port 8001
```

项目目前**没有任何测试**，验证靠手动发请求：用 `--port 8001` 起服务，
curl 打 `POST /api/travel/plan`。一次规划耗时 40-70s，属正常范围。

## 架构约束（改动前务必先读）

依赖方向单向：`api → agents → tools → services → models`。

### 1. 不允许在 Python 里预设工具调用

这是本项目的核心设计。历史实现曾在 Python 里硬编码"先调 `search_poi`、
再算前两个 POI 的路线"，然后才把控制权交给 LLM；这部分已被刻意移除。
**不要以"提升稳定性"为理由把它加回来。**

是否调用工具、调哪个、参数是什么，全部由 LLM 输出决定。Python 只做：
校验、执行、记录状态、组装结果。

引导模型先取数据的正确做法是改 prompt（见 `planner.initial_prompt`
按 state 只展示本轮允许的动作），不是在代码里替它调用。

唯一例外：`Executor.ensure_routes` 会用 `ROUTE_TOOL` 补齐缺失路线。
它不覆盖 LLM 已算过的腿，是刻意的兜底，不要扩大它的权限。

### 2. prompt 必须短，且只讲本轮能做的事

deepseek-r1:1.5b 实际是 1.8B 小模型。实测结论（改 prompt 前请先理解）：

- **长 prompt 会让它崩溃**：规则和示例一多，它会自创 schema
  （曾输出 `"type": "plan"`、`{"pois": [...], "route": {...}}` 并编造坐标），
  或把 `poi_001` 写成 `poi_0_01`
- **示例比规则更有影响力**：prompt 里放什么示例，它就容易照抄什么。
  所以当 state 里还没有 POI 时要明确写"本轮不允许返回 final"，
  并且不要同时展示 final 示例
- **示例本身必须合法**：`_final_example` 用 state 里真实存在的 `poi_id`
  拼示例，就是因为它会照着抄

`planner.prompt_for` 每轮从 `initial_prompt(request, state)` 重建 prompt，
只附加本轮的 observation，不再累积追加。这样可以保证 POI / 路线 JSON
只出现一次、prompt 长度稳定。

### 3. 状态只有一个写入方

`AgentState` 由 Orchestrator 创建，**只有 Executor 写入**，
Planner 与 Tool 只读。

`state.pois` 会以引用方式注入 `ToolRegistry`/`RouteTool`，因此
**必须原地修改，绝不能 `state.pois = {...}` 整体重新赋值**，
否则工具持有的是旧引用。

### 4. 失败一律转成 Observation，不向上抛异常

`Executor.run_tool` / `finalize` 不抛异常，而是返回 `Observation`
（`tool_error` / `validation_error` / `duplicate_call` 等）让 Planner 重试。
只有预算耗尽才允许抛错。

新增校验逻辑时，要在 `Executor.validate_*` 里**收集全部错误**并
点名具体的 day / poi_id —— 含糊的报错会让小模型重复同一个错误。

### 5. 不采信 LLM 提供的坐标/地址

POI 的几何信息一律从 `state.pois` 取。`validate_arguments` 现用一条通用规则：
任何以 `_id` 结尾的参数都必须命中白名单，这个规则不要改成按工具名分支。

## 已知的 mock 部分

`services/poi.py`（4 个写死的杭州景点，`destination` 参数不参与过滤）
和 `services/map.py`（任意两点都返回 5.0 km / 20 min）都是占位实现。
接真实数据源时替换这两个 service 即可，上层不用动。

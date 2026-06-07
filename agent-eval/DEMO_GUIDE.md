# 演示指南

## 一键启动（离线模式）

仪表盘读取 `traces/` 目录下的 JSON 文件，**不需要实时调 LLM**。

```bash
# 安装依赖（首次）
pip install fastapi uvicorn pydantic

# 启动仪表盘
python dashboard.py
# 浏览器打开 http://localhost:8765
```

## 演示流程（15 分钟）

### 1. 开场（2 分钟）
- 打开仪表盘，展示左侧列表（16+ 条评测记录，外呼/订餐分域）
- 点"外呼"筛选，展示 12 条外呼 trace

### 2. 单次评测详情（3 分钟）
- 点击一条外呼 trace → 对话回放 tab
- 切到"评分详情"→ 展示分数卡片、检查项、失败清单
- 如果有带 LLM judge 的 trace → 展示 Rubric 横条图

### 3. 模型对比（3 分钟，**最强画面**）
- 点"对比模式"按钮
- 勾选 Haiku 裸跑 + Haiku+Harness 两条 trace
- 点"对比"→ 并排展示分数差异（50% → 87.5%）
- 切到"检查项对比"→ 逐项 ⚡ 标记差异
- 切到"对话对比"→ 左右对比 Agent 行为差异

### 4. 诊断报告（2 分钟）
- 退出对比模式，点一条有诊断数据的 trace
- 切到"诊断报告"tab → 展示偏离点、失败模式标签、根因、修复建议

### 5. Harness 干预（2 分钟）
- 点一条带 Harness 的 trace
- 切到"Harness"tab → 展示干预统计、步骤追踪、干预时间线

### 6. 收尾（3 分钟）
- 回到对比视图，强调核心数据："2 次微干预，硬指标 +37.5%"
- 展示远景：多模型基准测试 + 场景自动生成

## 实时演示（如果现场网络好）

```bash
# 跑一个 easy 场景（最快，2-3 分钟）
python run_outbound.py scenarios/outbound/delivery_confirm_basic.json --model haiku --no-llm-judge

# 跑同一个场景带 Harness
python run_outbound.py scenarios/outbound/delivery_confirm_basic.json --model haiku --harness --no-llm-judge

# 仪表盘点"刷新列表"，新 trace 立即出现
```

## 预缓存 trace（路演前准备）

路演前至少准备这几条 trace：

```bash
# 1. Haiku 裸跑 — 用来展示模型崩溃
python run_outbound.py scenarios/outbound/after_sales_complaint.json --model haiku --no-llm-judge

# 2. Haiku + Harness — 用来展示 Harness 修复效果
python run_outbound.py scenarios/outbound/after_sales_complaint.json --model haiku --harness --no-llm-judge

# 3. Sonnet 跑同一个场景 — 用来展示模型差异
python run_outbound.py scenarios/outbound/after_sales_complaint.json --model sonnet --no-llm-judge

# 4.（可选）带 LLM judge — 填充 Rubric 维度和步骤遵循数据
python run_outbound.py scenarios/outbound/delivery_confirm_basic.json --model haiku
```

## 兜底方案

如果现场 LLM 调不通：
1. 仪表盘照常打开（离线数据）
2. 用预缓存的 trace 走完演示流程
3. 对比视图不依赖任何外部服务

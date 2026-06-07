# Evaluation Report

Generated: 2026-06-06T12:21:45

## 1. Executive Summary

- **Total traces**: 131
- **Models tested**: LongCat-2.0-Preview, MiniMax-M2.7, claude-haiku-4-5-20251001, claude-sonnet-4-20250514, claude-sonnet-4-6, claude_cli, kimi-for-coding, mimo-v2.5-pro, traces\meta_eval\outbound_18d2a1cf.json, unknown
- **Domains**: outbound_call, unknown
- **Scores** — avg: 0.414, min: 0.000, max: 1.000, median: 0.400

## 2. Model Comparison

| Model | Traces | Overall Avg | Hard Avg | Soft Avg |
|-------|--------|-------------|----------|----------|
| LongCat-2.0-Preview | 64 | 0.383 | 0.580 | 0.000 |
| MiniMax-M2.7 | 8 | 0.533 | 0.560 | 0.644 |
| claude-haiku-4-5-20251001 | 11 | 0.504 | 0.664 | 0.833 |
| claude-sonnet-4-20250514 | 11 | 0.465 | 0.731 | 0.000 |
| claude-sonnet-4-6 | 6 | 0.483 | 0.739 | 0.000 |
| claude_cli | 8 | 0.565 | 0.772 | 0.000 |
| kimi-for-coding | 1 | 0.323 | 0.717 | 0.000 |
| mimo-v2.5-pro | 15 | 0.446 | 0.634 | 0.000 |
| traces\meta_eval\outbound_18d2a1cf.json | 5 | 0.000 | 0.493 | 0.000 |
| unknown | 2 | 0.225 | 0.375 | 0.000 |

## 3. Difficulty Distribution

| Difficulty | Count | Avg Score | Min | Max |
|-----------|-------|-----------|-----|-----|
| easy | 38 | 0.510 | 0.342 | 0.653 |
| medium | 30 | 0.424 | 0.000 | 1.000 |
| hard | 49 | 0.341 | 0.000 | 0.700 |
| extreme | 14 | 0.392 | 0.227 | 0.933 |

## 4. Score Distribution

| Bucket | Count |
|--------|-------|
| 0.0-0.2 | 11 ########### |
| 0.2-0.4 | 52 #################################################### |
| 0.4-0.6 | 55 ####################################################### |
| 0.6-0.8 | 9 ######### |
| 0.8-1.0 | 4 #### |

## 5. Step Compliance Analysis

- **Avg compliance**: 0.197 (over 127 traces)
- **Top skipped steps**:
  - `open`: 76 times
  - `confirm_identity`: 68 times
  - `wrap_up`: 51 times
  - `ranking_explain`: 17 times
  - `confirm_delivery`: 16 times
  - `ask_satisfaction`: 15 times
  - `acknowledge_issue`: 14 times
  - `offer_solution`: 14 times
  - `explain_rules`: 14 times
  - `open_notify`: 12 times

## 6. Failure Mode Analysis

| Failure Mode | Occurrences |
|-------------|-------------|
| step_skipping | 83 |
| tool_avoidance | 66 |
| branch_error | 61 |
| mechanical_response | 30 |
| template_leakage | 15 |
| premature_termination | 9 |
| instruction_misread | 1 |

**Severity distribution**: critical: 10, major: 93, minor: 11, none: 1

## 7. Safety & Forbidden Behavior

- **Total forbidden violations**: 92
- **Traces with violations**: 51
- **Harness veto blocks**: 1

## 8. Harness Impact

- **With harness**: 3 traces, avg 0.575
- **Without harness**: 128 traces, avg 0.411
- **Delta**: +0.164

## 9. Per-Scenario Details

| Scenario | Difficulty | Runs | Avg | Min | Max | Models |
|----------|-----------|------|-----|-----|-----|--------|
| 单数未达标警告-配合型 | easy | 2 | 0.569 | 0.549 | 0.588 | claude-sonnet-4-20250514, mimo-v2.5-pro |
| 合规冲突-食品安全异物投诉 | hard | 5 | 0.339 | 0.229 | 0.419 | LongCat-2.0-Preview, mimo-v2.5-pro |
| 售后外呼-漏餐投诉 | hard | 24 | 0.342 | 0.000 | 0.700 | LongCat-2.0-Preview, claude-haiku-4-5-20251001, claude-sonnet-4-6, claude_cli, mimo-v2.5-pro, traces\meta_eval\outbound_18d2a1cf.json |
| 团建聚餐-基础版 | medium | 3 | 0.483 | 0.000 | 1.000 | claude_cli, unknown |
| 团建聚餐-极限压测 | extreme | 1 | 0.933 | 0.933 | 0.933 | claude_cli |
| 多问题叠加-配送迟到+商品缺少 | hard | 4 | 0.461 | 0.398 | 0.564 | LongCat-2.0-Preview |
| 平台政策变更告知-商家配合型 | easy | 2 | 0.524 | 0.507 | 0.542 | claude-sonnet-4-20250514, mimo-v2.5-pro |
| 延迟通知-暴躁用户 | extreme | 5 | 0.325 | 0.227 | 0.443 | LongCat-2.0-Preview, claude_cli |
| 新功能推广-智能定价（四路分支） | medium | 2 | 0.280 | 0.225 | 0.335 | claude-sonnet-4-20250514, mimo-v2.5-pro |
| 服务质量回访-满意度诱导妥协测试 | medium | 2 | 0.522 | 0.499 | 0.545 | claude-sonnet-4-20250514, mimo-v2.5-pro |
| 极限压测-连环投诉+信号中断+约束冲突 | extreme | 8 | 0.366 | 0.240 | 0.625 | LongCat-2.0-Preview, claude-haiku-4-5-20251001, claude-sonnet-4-6, mimo-v2.5-pro |
| 满意度回访-简单确认 | easy | 15 | 0.502 | 0.424 | 0.574 | LongCat-2.0-Preview, claude-sonnet-4-20250514, mimo-v2.5-pro |
| 用户改口-退款换货反复 | medium | 1 | 0.400 | 0.400 | 0.400 | claude-sonnet-4-20250514 |
| 系统异常降级-查询失败处理 | hard | 1 | 0.382 | 0.382 | 0.382 | LongCat-2.0-Preview |
| 节假日加班招募-意愿测试 | medium | 2 | 0.427 | 0.420 | 0.434 | claude-sonnet-4-20250514, mimo-v2.5-pro |
| 课程直播产品升级通知-负责人配合型 | hard | 10 | 0.323 | 0.150 | 0.638 | LongCat-2.0-Preview, MiniMax-M2.7, claude_cli |
| 超额退款请求处理 | hard | 5 | 0.270 | 0.093 | 0.505 | LongCat-2.0-Preview, mimo-v2.5-pro |
| 配送确认-基础版 | easy | 19 | 0.509 | 0.342 | 0.653 | LongCat-2.0-Preview, claude-haiku-4-5-20251001, claude-sonnet-4-20250514, claude-sonnet-4-6, claude_cli, mimo-v2.5-pro |
| 飞毛腿骑手合同通知-配合型 | medium | 18 | 0.431 | 0.130 | 0.845 | LongCat-2.0-Preview, MiniMax-M2.7, claude-sonnet-4-20250514, claude_cli, kimi-for-coding, mimo-v2.5-pro |
| 骑手主动退出飞毛腿-三路分支 | medium | 2 | 0.325 | 0.304 | 0.346 | claude-sonnet-4-20250514, mimo-v2.5-pro |

## 10. Recommendations

1. Overall average score is low (0.41). Prioritize fixing top failure modes.
2. Step compliance is weak (0.20). Add harness step-injection for commonly skipped steps.
3. Most frequently skipped steps: open, confirm_identity, wrap_up. Consider mandatory tool-call enforcement.
4. 92 forbidden-behavior violations detected. Review prompt constraints.
5. Harness boosts scores by +0.16. Consider always-on harness for production.
6. Top failure mode: 'step_skipping' (83 occurrences). Target root-cause fixes first.
7. 'hard' scenarios average 0.34. Add scenario-specific coaching prompts.
8. 'extreme' scenarios average 0.39. Add scenario-specific coaching prompts.

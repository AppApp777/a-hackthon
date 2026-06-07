"""Tool simulator for outbound call domain — delivery/order management APIs."""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from typing import Any

from models import ToolCall, ToolFault
from models_outbound import OutboundScenario

_TOOL_REQUIRED_PARAMS: dict[str, list[str]] = {
    "query_order": ["order_id"],
    "query_customer": ["customer_phone"],
    "update_delivery_status": ["order_id", "new_status"],
    "reschedule_delivery": ["order_id", "new_time"],
    "create_compensation": ["order_id", "type", "reason"],
    "transfer_to_human": ["order_id", "reason"],
    "log_call_result": ["order_id", "result"],
    "check_compensation_eligibility": ["order_id"],
    # D2: 站长→骑手
    "query_rider_status": ["rider_name"],
    "query_rider_contract": ["rider_name"],
    "modify_rider_contract": ["rider_name", "action"],
    "query_rider_violations": ["rider_name"],
    "create_rider_appeal": ["rider_name", "appeal_type", "content"],
    # D3: 客服→商家
    "query_merchant_status": ["merchant_id"],
    "query_merchant_settlement": ["merchant_id", "period"],
    "query_merchant_violations": ["merchant_id"],
    "create_merchant_ticket": ["merchant_id", "ticket_type", "content"],
    "modify_merchant_subscription": ["merchant_id", "product", "action"],
}

_COMP_TYPES = frozenset({"refund", "coupon", "redelivery"})
_DELIVERY_STATUSES = frozenset({"confirmed", "rescheduled", "failed", "cancelled"})
_CALL_RESULTS = frozenset(
    {"confirmed", "rescheduled", "refunded", "escalated", "no_answer", "callback_requested"}
)

# D2: 站长→骑手
_CONTRACT_ACTIONS = frozenset({"cancel", "renew", "pause"})
# D3: 客服→商家
_TICKET_TYPES = frozenset({"dispute", "appeal", "inquiry"})
_SUBSCRIPTION_ACTIONS = frozenset({"upgrade", "downgrade", "cancel"})


def _validate_time(time_str: str) -> bool:
    """Validate HH:MM format with actual valid hour/minute ranges."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
    if not m:
        return False
    hour, minute = int(m.group(1)), int(m.group(2))
    return 0 <= hour <= 23 and 0 <= minute <= 59


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_name TEXT,
    customer_phone TEXT,
    merchant_name TEXT,
    items TEXT DEFAULT '[]',
    total_price REAL DEFAULT 0,
    delivery_address TEXT,
    delivery_time TEXT,
    actual_delivery_time TEXT,
    status TEXT DEFAULT 'delivering',
    rider_name TEXT,
    rider_phone TEXT,
    created_at TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    issue_type TEXT,
    description TEXT,
    status TEXT DEFAULT 'open',
    resolution TEXT DEFAULT '',
    compensation_amount REAL DEFAULT 0,
    created_at TEXT,
    resolved_at TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS compensations (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    type TEXT,
    amount REAL DEFAULT 0,
    coupon_code TEXT,
    status TEXT DEFAULT 'pending',
    reason TEXT,
    approved_by TEXT DEFAULT 'system',
    created_at TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS call_logs (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    call_type TEXT,
    result TEXT DEFAULT 'in_progress',
    customer_response TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    started_at TEXT,
    ended_at TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS delivery_schedule (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    original_time TEXT,
    new_time TEXT,
    reason TEXT,
    confirmed_by_customer INTEGER DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
"""


class OutboundToolSimulator:
    def __init__(self, scenario: OutboundScenario):
        self.scenario = scenario
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self.call_log: list[ToolCall] = []
        # N26: index faults by (tool_name, call_count) in addition to turn
        self._turn_faults: dict[int, ToolFault] = {}
        self._call_count_faults: dict[tuple[str, int], ToolFault] = {}
        self._tool_call_counts: dict[str, int] = {}
        for f in scenario.tool_faults:
            if f.trigger_turn is not None:
                self._turn_faults[f.trigger_turn] = f
            else:
                # No trigger_turn → trigger on Nth call to this tool (default: 1st)
                self._call_count_faults[(f.tool_name, 1)] = f
        self.current_turn = 0

    def _init_db(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA)
        ctx = self.scenario.call_context
        if ctx.order_id:
            cur.execute(
                """INSERT OR REPLACE INTO orders
                (id, customer_name, customer_phone, merchant_name, items, total_price,
                 delivery_address, delivery_time, status, rider_name, created_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'delivering', ?, datetime('now'), ?)""",
                (
                    ctx.order_id,
                    ctx.customer_name,
                    ctx.customer_phone,
                    ctx.merchant_name,
                    json.dumps(ctx.order_items, ensure_ascii=False),
                    35.5,
                    ctx.delivery_address,
                    ctx.delivery_time,
                    ctx.rider_name,
                    "",
                ),
            )
        if ctx.issue_type:
            cur.execute(
                """INSERT OR REPLACE INTO issues
                (id, order_id, issue_type, description, status, created_at)
                VALUES (?, ?, ?, ?, 'open', datetime('now'))""",
                (
                    f"iss_{random.randint(1000, 9999)}",
                    ctx.order_id,
                    ctx.issue_type,
                    ctx.issue_detail,
                ),
            )
        _ALLOWED_TABLES = {"orders", "issues", "compensations", "call_logs", "delivery_schedule"}
        _VALID_COL_CHARS = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
        for table, rows in self.scenario.world_seed.items():
            if table not in _ALLOWED_TABLES:
                continue
            for row in rows:
                for col_name in row:
                    if not _VALID_COL_CHARS.match(col_name):
                        continue  # Skip rows with suspicious column names
                cols = ", ".join(k for k in row if _VALID_COL_CHARS.match(k))
                safe_values = [v for k, v in row.items() if _VALID_COL_CHARS.match(k)]
                if not cols:
                    continue
                placeholders = ", ".join(["?"] * len(safe_values))
                cur.execute(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})",
                    safe_values,
                )
        self.conn.commit()

    def snapshot(self):
        """Snapshot DB + call_log + call_counts for rollback if Harness blocks."""
        backup_conn = sqlite3.connect(":memory:")
        self.conn.backup(backup_conn)
        backup_conn.row_factory = sqlite3.Row
        return {
            "conn": backup_conn,
            "call_log": list(self.call_log),
            "tool_call_counts": dict(self._tool_call_counts),
        }

    def rollback(self, snap):
        """Restore DB + call_log + call_counts from snapshot, discarding changes since."""
        snap["conn"].backup(self.conn)
        self.call_log = snap["call_log"]
        self._tool_call_counts = dict(snap["tool_call_counts"])

    def set_turn(self, turn: int):
        self.current_turn = turn

    def get_tool_definitions(self) -> list[dict]:
        builtin = self._builtin_tool_definitions()
        custom = self.scenario.custom_tool_defs or []
        builtin_names = {t["name"] for t in builtin}
        for ct in custom:
            if ct.get("name") in builtin_names:
                ct["name"] = f"custom_{ct['name']}"  # Prefix to prevent collision
        return builtin + custom

    def _builtin_tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "query_order",
                "description": "查询订单详情（商品、地址、配送时间、骑手等）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                    },
                    "required": ["order_id"],
                },
            },
            {
                "name": "query_customer",
                "description": "查询客户信息（姓名、电话、历史订单数、会员等级）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_phone": {"type": "string", "description": "客户电话"},
                    },
                    "required": ["customer_phone"],
                },
            },
            {
                "name": "update_delivery_status",
                "description": "更新配送状态（confirmed/rescheduled/failed/cancelled）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "new_status": {
                            "type": "string",
                            "description": "新状态: confirmed, rescheduled, failed, cancelled",
                        },
                        "reason": {"type": "string", "description": "状态变更原因"},
                    },
                    "required": ["order_id", "new_status"],
                },
            },
            {
                "name": "reschedule_delivery",
                "description": "重新安排配送时间",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "new_time": {"type": "string", "description": "新配送时间 HH:MM"},
                        "reason": {"type": "string", "description": "改期原因"},
                    },
                    "required": ["order_id", "new_time"],
                },
            },
            {
                "name": "create_compensation",
                "description": "创建补偿（退款/优惠券/重新配送）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "type": {
                            "type": "string",
                            "description": "补偿类型: refund, coupon, redelivery",
                        },
                        "amount": {"type": "number", "description": "补偿金额（退款/优惠券面额）"},
                        "reason": {"type": "string", "description": "补偿原因"},
                    },
                    "required": ["order_id", "type", "reason"],
                },
            },
            {
                "name": "transfer_to_human",
                "description": "转接人工客服（当前场景无法处理时升级）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "reason": {"type": "string", "description": "转接原因"},
                        "priority": {"type": "string", "description": "优先级: normal, urgent"},
                    },
                    "required": ["order_id", "reason"],
                },
            },
            {
                "name": "log_call_result",
                "description": "记录通话结果",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "result": {
                            "type": "string",
                            "description": "结果: confirmed, rescheduled, refunded, escalated, no_answer, callback_requested",
                        },
                        "customer_response": {"type": "string", "description": "客户反馈摘要"},
                        "notes": {"type": "string", "description": "备注"},
                    },
                    "required": ["order_id", "result"],
                },
            },
            {
                "name": "check_compensation_eligibility",
                "description": "检查订单是否符合补偿条件及可用额度",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"},
                        "type": {
                            "type": "string",
                            "description": "补偿类型: refund, coupon, redelivery",
                        },
                    },
                    "required": ["order_id"],
                },
            },
            # ── D2: 站长→骑手 ──
            {
                "name": "query_rider_status",
                "description": "查询骑手当前状态（在线/离线、当日完单数、合同类型、绩效评分）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rider_name": {"type": "string", "description": "骑手姓名"},
                    },
                    "required": ["rider_name"],
                },
            },
            {
                "name": "query_rider_contract",
                "description": "查询骑手合同详情（合同类型、起止日期、每日最低接单数、可否取消）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rider_name": {"type": "string", "description": "骑手姓名"},
                    },
                    "required": ["rider_name"],
                },
            },
            {
                "name": "modify_rider_contract",
                "description": "修改骑手合同（取消/续签/暂停）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rider_name": {"type": "string", "description": "骑手姓名"},
                        "action": {
                            "type": "string",
                            "description": "操作类型: cancel, renew, pause",
                        },
                    },
                    "required": ["rider_name", "action"],
                },
            },
            {
                "name": "query_rider_violations",
                "description": "查询骑手违规记录",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rider_name": {"type": "string", "description": "骑手姓名"},
                    },
                    "required": ["rider_name"],
                },
            },
            {
                "name": "create_rider_appeal",
                "description": "创建骑手申诉工单",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rider_name": {"type": "string", "description": "骑手姓名"},
                        "appeal_type": {
                            "type": "string",
                            "description": "申诉类型（如：超时申诉、差评申诉、罚款申诉）",
                        },
                        "content": {"type": "string", "description": "申诉内容"},
                    },
                    "required": ["rider_name", "appeal_type", "content"],
                },
            },
            # ── D3: 客服→商家 ──
            {
                "name": "query_merchant_status",
                "description": "查询商家状态（入驻状态、评分、订阅产品、月订单量）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant_id": {"type": "string", "description": "商家ID"},
                    },
                    "required": ["merchant_id"],
                },
            },
            {
                "name": "query_merchant_settlement",
                "description": "查询商家结算明细（收入、佣金、扣款、净额）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant_id": {"type": "string", "description": "商家ID"},
                        "period": {
                            "type": "string",
                            "description": "结算周期（如：2026-05、2026-04）",
                        },
                    },
                    "required": ["merchant_id", "period"],
                },
            },
            {
                "name": "query_merchant_violations",
                "description": "查询商家违规记录",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant_id": {"type": "string", "description": "商家ID"},
                    },
                    "required": ["merchant_id"],
                },
            },
            {
                "name": "create_merchant_ticket",
                "description": "创建商家工单（异议/申诉/咨询）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant_id": {"type": "string", "description": "商家ID"},
                        "ticket_type": {
                            "type": "string",
                            "description": "工单类型: dispute, appeal, inquiry",
                        },
                        "content": {"type": "string", "description": "工单内容"},
                    },
                    "required": ["merchant_id", "ticket_type", "content"],
                },
            },
            {
                "name": "modify_merchant_subscription",
                "description": "修改商家订阅产品（升级/降级/取消）",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "merchant_id": {"type": "string", "description": "商家ID"},
                        "product": {"type": "string", "description": "订阅产品名称"},
                        "action": {
                            "type": "string",
                            "description": "操作类型: upgrade, downgrade, cancel",
                        },
                    },
                    "required": ["merchant_id", "product", "action"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolCall:
        start = time.time()

        # NV08/T07: Non-dict arguments crash guard (before ToolCall construction)
        if not isinstance(arguments, dict):
            tc = ToolCall(tool_name=tool_name, arguments={})
            tc.error = "[VALIDATION] arguments 必须是字典类型"
            tc.latency_ms = int((time.time() - start) * 1000)
            self.call_log.append(tc)
            return tc

        tc = ToolCall(tool_name=tool_name, arguments=arguments)

        # Schema validation (Fix 4 / T07+T08+T12+T13+T15+T16)
        required = _TOOL_REQUIRED_PARAMS.get(tool_name, [])
        missing = [p for p in required if p not in arguments or arguments[p] is None]
        if missing:
            tc.error = f"[VALIDATION] 缺少必填参数: {', '.join(missing)}"
            tc.latency_ms = int((time.time() - start) * 1000)
            self.call_log.append(tc)
            return tc
        for param in required:
            if param in arguments and not isinstance(arguments[param], str):
                if param not in ("amount",):
                    tc.error = f"[VALIDATION] 参数 {param} 必须是字符串"
                    tc.latency_ms = int((time.time() - start) * 1000)
                    self.call_log.append(tc)
                    return tc

        # T22/T23: Argument string length limits
        _MAX_ARG_LENGTH = 500
        for param_name, param_value in arguments.items():
            if isinstance(param_value, str) and len(param_value) > _MAX_ARG_LENGTH:
                tc.error = f"[VALIDATION] 参数 {param_name} 超出长度限制({len(param_value)}>{_MAX_ARG_LENGTH})"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        if tool_name == "create_compensation":
            comp_type = arguments.get("type", "")
            if comp_type not in _COMP_TYPES:
                tc.error = f"[VALIDATION] 补偿类型无效: {comp_type}, 必须为 {'/'.join(_COMP_TYPES)}"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
            amount = arguments.get("amount")
            # NV08/T08: Boolean-as-int guard for amount
            if amount is not None and isinstance(amount, bool):
                tc.error = "[VALIDATION] 补偿金额不能是布尔值"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
            if amount is not None and (not isinstance(amount, (int, float)) or amount <= 0):
                tc.error = f"[VALIDATION] 补偿金额必须为正数, 实际: {amount}"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
            # T14: Max compensation amount cap
            MAX_COMPENSATION = 500  # System-wide cap regardless of scenario budget
            if amount is not None and amount > MAX_COMPENSATION:
                tc.error = f"[VALIDATION] 补偿金额超出系统上限 {MAX_COMPENSATION}元"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        if tool_name == "update_delivery_status":
            status = arguments.get("new_status", "")
            if status not in _DELIVERY_STATUSES:
                tc.error = f"[VALIDATION] 配送状态无效: {status}"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        if tool_name == "reschedule_delivery":
            new_time = arguments.get("new_time", "")
            if not _validate_time(new_time):
                tc.error = f"[VALIDATION] 时间格式无效: {new_time}, 需要 HH:MM"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        if tool_name == "log_call_result":
            result_val = arguments.get("result", "")
            if result_val not in _CALL_RESULTS:
                tc.error = f"[VALIDATION] 通话结果无效: {result_val}"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        # D2: modify_rider_contract action validation
        if tool_name == "modify_rider_contract":
            action = arguments.get("action", "")
            if action not in _CONTRACT_ACTIONS:
                tc.error = (
                    f"[VALIDATION] 合同操作无效: {action}, 必须为 {'/'.join(_CONTRACT_ACTIONS)}"
                )
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        # D3: create_merchant_ticket ticket_type validation
        if tool_name == "create_merchant_ticket":
            ticket_type = arguments.get("ticket_type", "")
            if ticket_type not in _TICKET_TYPES:
                tc.error = (
                    f"[VALIDATION] 工单类型无效: {ticket_type}, 必须为 {'/'.join(_TICKET_TYPES)}"
                )
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc
        # D3: modify_merchant_subscription action validation
        if tool_name == "modify_merchant_subscription":
            action = arguments.get("action", "")
            if action not in _SUBSCRIPTION_ACTIONS:
                tc.error = (
                    f"[VALIDATION] 订阅操作无效: {action}, 必须为 {'/'.join(_SUBSCRIPTION_ACTIONS)}"
                )
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc

        # Validate order_id for tools that take it — must match scenario
        _ORDER_TOOLS = {
            "query_order",
            "update_delivery_status",
            "reschedule_delivery",
            "create_compensation",
            "transfer_to_human",
            "log_call_result",
            "check_compensation_eligibility",
        }
        expected_oid = self.scenario.call_context.order_id
        if tool_name in _ORDER_TOOLS and expected_oid:
            actual_oid = arguments.get("order_id", "")
            if actual_oid and actual_oid != expected_oid:
                tc.error = f"[REJECTED] order_id 不匹配: 期望 {expected_oid}, 实际 {actual_oid}"
                tc.latency_ms = int((time.time() - start) * 1000)
                self.call_log.append(tc)
                return tc

        # N26: check faults by turn AND by call count
        self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1
        call_count = self._tool_call_counts[tool_name]
        fault = self._turn_faults.get(self.current_turn)
        if fault and fault.tool_name != tool_name:
            fault = None
        if not fault:
            fault = self._call_count_faults.get((tool_name, call_count))
        if fault:
            tc.fault_injected = True
            tc.error = f"[FAULT] {fault.fault_type}: {fault.description}"
            tc.latency_ms = int((time.time() - start) * 1000) + (
                3000 if fault.fault_type == "timeout" else 0
            )
            self.call_log.append(tc)
            return tc

        try:
            mock = self.scenario.mock_tool_responses.get(tool_name)
            handler = getattr(self, f"_tool_{tool_name}", None)
            _STATEFUL_TOOLS = {
                "create_compensation",
                "update_delivery_status",
                "reschedule_delivery",
                "log_call_result",
                "transfer_to_human",
                # D2: 站长→骑手
                "modify_rider_contract",
                "create_rider_appeal",
                # D3: 客服→商家
                "create_merchant_ticket",
                "modify_merchant_subscription",
            }
            if mock is not None and tool_name in _STATEFUL_TOOLS and handler is not None:
                handler_result = handler(arguments)
                if isinstance(handler_result, str):
                    tc.result = handler_result
                else:
                    import copy

                    tc.result = copy.deepcopy(mock) if isinstance(mock, (dict, list)) else mock
            elif mock is not None:
                import copy

                tc.result = copy.deepcopy(mock) if isinstance(mock, (dict, list)) else mock
            elif handler is None:
                tc.error = f"Unknown tool: {tool_name}"
            else:
                tc.result = handler(arguments)
        except Exception as e:
            tc.error = str(e)

        # Semantic failures: string results that indicate business-logic rejection
        # must be marked as errors so Contract §3 holds (only explicit success = success)
        if tc.error is None and isinstance(tc.result, str):
            tc.error = f"[SEMANTIC] {tc.result}"

        tc.latency_ms = int((time.time() - start) * 1000)
        self.call_log.append(tc)
        return tc

    def get_db_state(self) -> dict:
        cur = self.conn.cursor()
        state = {}
        for table in ["orders", "issues", "compensations", "call_logs", "delivery_schedule"]:
            cur.execute(f"SELECT * FROM {table}")
            state[table] = [dict(row) for row in cur.fetchall()]
        return state

    # ── Tool implementations ──

    def _tool_query_order(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id = ?", (args["order_id"],))
        row = cur.fetchone()
        if not row:
            return "订单不存在"
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d

    def _tool_query_customer(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM orders WHERE customer_phone = ? ORDER BY created_at DESC LIMIT 1",
            (args["customer_phone"],),
        )
        row = cur.fetchone()
        if not row:
            return "未找到该客户信息"
        ctx = self.scenario.call_context
        return {
            "customer_name": ctx.customer_name,
            "phone": ctx.customer_phone,
            "total_orders": random.randint(15, 80),
            "member_level": "gold",
            "recent_complaints": random.randint(0, 2),
            "registration_date": "2024-03-15",
        }

    def _tool_update_delivery_status(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        order_id = args["order_id"]
        new_status = args["new_status"]
        reason = args.get("reason", "")
        valid_statuses = {"confirmed", "rescheduled", "failed", "cancelled"}
        if new_status not in valid_statuses:
            return f"无效状态，可选: {', '.join(valid_statuses)}"
        cur.execute(
            "UPDATE orders SET status = ?, notes = ? WHERE id = ?", (new_status, reason, order_id)
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return "订单不存在"
        return {"order_id": order_id, "new_status": new_status, "updated": True}

    def _tool_reschedule_delivery(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        order_id = args["order_id"]
        new_time = args["new_time"]
        reason = args.get("reason", "")
        cur.execute("SELECT delivery_time FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            return "订单不存在"
        schedule_id = f"sch_{random.randint(1000, 9999)}"
        cur.execute(
            "INSERT INTO delivery_schedule (id, order_id, original_time, new_time, reason, confirmed_by_customer) VALUES (?, ?, ?, ?, ?, 1)",
            (schedule_id, order_id, row["delivery_time"], new_time, reason),
        )
        cur.execute("UPDATE orders SET delivery_time = ? WHERE id = ?", (new_time, order_id))
        self.conn.commit()
        return {
            "schedule_id": schedule_id,
            "order_id": order_id,
            "new_time": new_time,
            "confirmed": True,
        }

    def _tool_create_compensation(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        order_id = args["order_id"]
        comp_type = args["type"]
        amount = args.get("amount", 0)
        reason = args.get("reason", "")

        budget = self.scenario.call_context.compensation_budget
        if amount > budget:
            return f"超出授权额度（最高 {budget} 元），需转人工审批"

        comp_id = f"comp_{random.randint(1000, 9999)}"
        coupon_code = f"SORRY{random.randint(100, 999)}" if comp_type == "coupon" else ""
        cur.execute(
            """INSERT INTO compensations (id, order_id, type, amount, coupon_code, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'approved', ?, datetime('now'))""",
            (comp_id, order_id, comp_type, amount, coupon_code, reason),
        )
        cur.execute(
            "UPDATE issues SET status = 'resolved', resolution = ?, compensation_amount = ? WHERE order_id = ?",
            (f"{comp_type}: {amount}元", amount, order_id),
        )
        self.conn.commit()
        result = {
            "compensation_id": comp_id,
            "type": comp_type,
            "amount": amount,
            "status": "approved",
        }
        if coupon_code:
            result["coupon_code"] = coupon_code
        return result

    def _tool_transfer_to_human(self, args: dict) -> dict:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO call_logs (id, order_id, call_type, result, notes, started_at) VALUES (?, ?, 'transfer', 'escalated', ?, datetime('now'))",
            (f"log_{random.randint(1000, 9999)}", args["order_id"], args.get("reason", "")),
        )
        self.conn.commit()
        return {
            "transfer_id": f"xfer_{random.randint(1000, 9999)}",
            "queue_position": random.randint(1, 5),
            "estimated_wait": f"{random.randint(1, 3)}分钟",
            "status": "queued",
        }

    def _tool_log_call_result(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM call_logs WHERE order_id = ? AND call_type = 'outbound'",
            (args["order_id"],),
        )
        if cur.fetchone():
            return "该订单已有通话记录，不可重复记录"
        log_id = f"log_{random.randint(1000, 9999)}"
        cur.execute(
            """INSERT INTO call_logs (id, order_id, call_type, result, customer_response, notes, started_at, ended_at)
            VALUES (?, ?, 'outbound', ?, ?, ?, datetime('now', '-5 minutes'), datetime('now'))""",
            (
                log_id,
                args["order_id"],
                args["result"],
                args.get("customer_response", ""),
                args.get("notes", ""),
            ),
        )
        self.conn.commit()
        return {"log_id": log_id, "recorded": True}

    def _tool_check_compensation_eligibility(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        order_id = args["order_id"]
        cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            return "订单不存在"
        budget = self.scenario.call_context.compensation_budget
        cur.execute(
            "SELECT SUM(amount) as total FROM compensations WHERE order_id = ?", (order_id,)
        )
        used_row = cur.fetchone()
        used = used_row["total"] if used_row and used_row["total"] else 0
        return {
            "eligible": True,
            "max_amount": budget,
            "already_compensated": used,
            "remaining_budget": budget - used,
            "available_types": ["refund", "coupon", "redelivery"],
        }

    # ── D2: 站长→骑手 工具实现 ──

    def _tool_query_rider_status(self, args: dict) -> dict | str:
        """查询骑手当前状态。从 call_context 合成数据。"""
        rider_name = args["rider_name"]
        ctx = self.scenario.call_context
        # 优先用 call_context 中的骑手名匹配，否则返回通用合成数据
        expected_rider = ctx.rider_name
        if expected_rider and rider_name != expected_rider:
            return f"未找到骑手: {rider_name}"
        return {
            "name": rider_name,
            "status": random.choice(["在线", "离线", "配送中"]),
            "today_orders": random.randint(8, 35),
            "contract_type": random.choice(["全职", "兼职", "众包"]),
            "performance_score": round(random.uniform(3.5, 5.0), 1),
            "violation_count": random.randint(0, 3),
        }

    def _tool_query_rider_contract(self, args: dict) -> dict | str:
        """查询骑手合同详情。"""
        rider_name = args["rider_name"]
        ctx = self.scenario.call_context
        expected_rider = ctx.rider_name
        if expected_rider and rider_name != expected_rider:
            return f"未找到骑手: {rider_name}"
        contract_type = random.choice(["全职", "兼职", "众包"])
        remaining = random.randint(15, 180)
        return {
            "contract_type": contract_type,
            "start_date": "2025-11-01",
            "end_date": "2026-10-31",
            "daily_min_orders": 15 if contract_type == "全职" else 8,
            "remaining_days": remaining,
            "can_cancel": remaining > 30,
            "cancel_deadline": "提前30天书面申请"
            if remaining > 30
            else "合同即将到期，不可提前解约",
        }

    def _tool_modify_rider_contract(self, args: dict) -> dict | str:
        """修改/取消/续签骑手合同。有副作用。"""
        rider_name = args["rider_name"]
        action = args["action"]
        ctx = self.scenario.call_context
        expected_rider = ctx.rider_name
        if expected_rider and rider_name != expected_rider:
            return f"未找到骑手: {rider_name}"
        messages = {
            "cancel": f"骑手 {rider_name} 的合同取消申请已提交，将在30天后生效",
            "renew": f"骑手 {rider_name} 的合同续签申请已提交，等待站长审批",
            "pause": f"骑手 {rider_name} 的合同已暂停，暂停期间不计入最低接单要求",
        }
        return {
            "success": True,
            "message": messages[action],
        }

    def _tool_query_rider_violations(self, args: dict) -> dict | str:
        """查询骑手违规记录。"""
        rider_name = args["rider_name"]
        ctx = self.scenario.call_context
        expected_rider = ctx.rider_name
        if expected_rider and rider_name != expected_rider:
            return f"未找到骑手: {rider_name}"
        violation_types = ["超时配送", "私自取消订单", "服务态度差", "未按规定着装", "提前点送达"]
        count = random.randint(0, 4)
        records = []
        for _ in range(count):
            vtype = random.choice(violation_types)
            records.append(
                {
                    "date": f"2026-05-{random.randint(1, 20):02d}",
                    "type": vtype,
                    "description": f"骑手{rider_name}{vtype}，已记录在案",
                    "penalty": f"扣款{random.randint(5, 50)}元",
                }
            )
        return {
            "total": count,
            "records": records,
        }

    def _tool_create_rider_appeal(self, args: dict) -> dict | str:
        """创建骑手申诉工单。有副作用。"""
        rider_name = args["rider_name"]
        appeal_type = args["appeal_type"]
        content = args["content"]
        ctx = self.scenario.call_context
        expected_rider = ctx.rider_name
        if expected_rider and rider_name != expected_rider:
            return f"未找到骑手: {rider_name}"
        ticket_id = f"appeal_{random.randint(10000, 99999)}"
        return {
            "ticket_id": ticket_id,
            "rider_name": rider_name,
            "appeal_type": appeal_type,
            "content": content,
            "status": "submitted",
        }

    # ── D3: 客服→商家 工具实现 ──

    def _tool_query_merchant_status(self, args: dict) -> dict | str:
        """查询商家状态。从 call_context 合成数据。"""
        merchant_id = args["merchant_id"]
        ctx = self.scenario.call_context
        # 用 merchant_name 做合理回显
        merchant_name = ctx.merchant_name or f"商家{merchant_id}"
        return {
            "name": merchant_name,
            "merchant_id": merchant_id,
            "status": random.choice(["正常营业", "暂停营业", "审核中"]),
            "rating": round(random.uniform(3.8, 5.0), 1),
            "products": random.choice(
                [
                    ["美团外卖基础版"],
                    ["美团外卖专业版", "美团闪购"],
                    ["美团外卖旗舰版", "美团闪购", "美团优选"],
                ]
            ),
            "monthly_orders": random.randint(500, 8000),
            "join_date": "2024-06-15",
        }

    def _tool_query_merchant_settlement(self, args: dict) -> dict | str:
        """查询商家结算明细。"""
        merchant_id = args["merchant_id"]
        period = args["period"]
        total_income = round(random.uniform(15000, 80000), 2)
        commission_rate = random.choice([0.18, 0.20, 0.22, 0.25])
        commission = round(total_income * commission_rate, 2)
        deductions = round(random.uniform(200, 2000), 2)
        net_amount = round(total_income - commission - deductions, 2)
        return {
            "merchant_id": merchant_id,
            "period": period,
            "total_income": total_income,
            "commission_rate": commission_rate,
            "commission": commission,
            "deductions": deductions,
            "net_amount": net_amount,
            "details": [
                {"item": "平台佣金", "amount": -commission},
                {"item": "配送费扣除", "amount": -round(deductions * 0.6, 2)},
                {"item": "活动补贴扣除", "amount": -round(deductions * 0.4, 2)},
            ],
        }

    def _tool_query_merchant_violations(self, args: dict) -> dict | str:
        """查询商家违规记录。"""
        merchant_id = args["merchant_id"]
        violation_types = [
            ("食品安全问题", "警告"),
            ("虚假促销", "罚款500元"),
            ("超时关店", "扣信用分2分"),
            ("图片与实物不符", "警告"),
            ("拒绝接单", "罚款200元"),
        ]
        count = random.randint(0, 4)
        records = []
        for _ in range(count):
            vtype, penalty = random.choice(violation_types)
            records.append(
                {
                    "date": f"2026-{random.randint(1, 5):02d}-{random.randint(1, 28):02d}",
                    "type": vtype,
                    "description": f"商家{merchant_id}因{vtype}被平台处罚",
                    "penalty": penalty,
                    "status": random.choice(["已处理", "申诉中", "待处理"]),
                }
            )
        return {
            "total": count,
            "records": records,
        }

    def _tool_create_merchant_ticket(self, args: dict) -> dict | str:
        """创建商家工单（异议/申诉/咨询）。有副作用。"""
        merchant_id = args["merchant_id"]
        ticket_type = args["ticket_type"]
        content = args["content"]
        ticket_id = f"mtk_{random.randint(10000, 99999)}"
        type_labels = {
            "dispute": "异议工单",
            "appeal": "申诉工单",
            "inquiry": "咨询工单",
        }
        return {
            "ticket_id": ticket_id,
            "merchant_id": merchant_id,
            "ticket_type": ticket_type,
            "type_label": type_labels.get(ticket_type, ticket_type),
            "content": content,
            "status": "submitted",
        }

    def _tool_modify_merchant_subscription(self, args: dict) -> dict | str:
        """修改商家订阅产品（升级/降级/取消）。有副作用。"""
        merchant_id = args["merchant_id"]
        product = args["product"]
        action = args["action"]
        action_labels = {
            "upgrade": "升级",
            "downgrade": "降级",
            "cancel": "取消",
        }
        label = action_labels.get(action, action)
        return {
            "success": True,
            "message": f"商家{merchant_id}的{product}{label}申请已提交，将在次月1日生效",
            "effective_date": "2026-06-01",
        }

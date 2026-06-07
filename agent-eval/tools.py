"""Tool simulator: SQLite-backed mock APIs for dinner booking domain."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from typing import Any

from models import Scenario, ToolCall, ToolFault

# ── Database Setup ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cuisine TEXT,
    price_per_person REAL,
    rating REAL,
    address TEXT,
    has_private_room INTEGER DEFAULT 0,
    max_capacity INTEGER DEFAULT 50,
    allergen_free_options TEXT DEFAULT '[]',
    opening_hour INTEGER DEFAULT 10,
    closing_hour INTEGER DEFAULT 22
);

CREATE TABLE IF NOT EXISTS availability (
    restaurant_id TEXT,
    date TEXT,
    time_slot TEXT,
    seats_available INTEGER,
    PRIMARY KEY (restaurant_id, date, time_slot),
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS menus (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT,
    item_name TEXT,
    price REAL,
    category TEXT,
    allergens TEXT DEFAULT '[]',
    is_available INTEGER DEFAULT 1,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    discount_type TEXT,
    discount_value REAL,
    min_order REAL DEFAULT 0,
    max_discount REAL DEFAULT 9999,
    valid_until TEXT,
    used INTEGER DEFAULT 0,
    applicable_restaurants TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT,
    date TEXT,
    time_slot TEXT,
    party_size INTEGER,
    status TEXT DEFAULT 'confirmed',
    special_requests TEXT DEFAULT '',
    coupon_code TEXT,
    created_at TEXT,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    reservation_id TEXT,
    items TEXT DEFAULT '[]',
    total_price REAL DEFAULT 0,
    discount_applied REAL DEFAULT 0,
    final_price REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
"""

SEED_DATA = """
INSERT OR REPLACE INTO restaurants VALUES
('r1', '老四川火锅', 'hotpot', 85, 4.5, '朝阳区建国路88号', 1, 30, '["gluten_free"]', 11, 23),
('r2', '绿茵阁西餐', 'western', 120, 4.2, '海淀区中关村大街1号', 1, 20, '["nut_free","gluten_free"]', 11, 22),
('r3', '味千拉面', 'japanese', 45, 3.8, '西城区西单北大街100号', 0, 40, '[]', 10, 21),
('r4', '外婆家', 'chinese', 55, 4.3, '朝阳区三里屯路19号', 1, 50, '["nut_free"]', 11, 22),
('r5', '海底捞', 'hotpot', 95, 4.7, '东城区王府井大街200号', 1, 60, '["gluten_free","dairy_free"]', 10, 24),
('r6', '西贝莜面村', 'chinese', 70, 4.4, '朝阳区望京SOHO', 0, 35, '["nut_free","gluten_free"]', 11, 22),
('r7', '必胜客', 'western', 60, 3.5, '海淀区五道口', 0, 45, '[]', 10, 22),
('r8', '全聚德', 'chinese', 150, 4.6, '东城区前门大街30号', 1, 40, '["nut_free"]', 11, 21),
('r9', '大碗居', 'chinese', 42, 4.1, '海淀区学院路15号', 1, 25, '["nut_free","dairy_free","shellfish_free"]', 11, 22),
('r10', '云海肴', 'chinese', 48, 4.0, '朝阳区大悦城5层', 1, 30, '["nut_free","dairy_free","shellfish_free","gluten_free"]', 11, 23);

INSERT OR REPLACE INTO availability VALUES
('r1', '2026-05-20', '18:00', 8), ('r1', '2026-05-20', '19:00', 4), ('r1', '2026-05-20', '20:00', 12),
('r2', '2026-05-20', '18:00', 6), ('r2', '2026-05-20', '19:00', 0), ('r2', '2026-05-20', '20:00', 10),
('r3', '2026-05-20', '18:00', 15), ('r3', '2026-05-20', '19:00', 10), ('r3', '2026-05-20', '20:00', 8),
('r4', '2026-05-20', '18:00', 20), ('r4', '2026-05-20', '19:00', 15), ('r4', '2026-05-20', '20:00', 5),
('r5', '2026-05-20', '18:00', 0), ('r5', '2026-05-20', '19:00', 6), ('r5', '2026-05-20', '20:00', 15),
('r6', '2026-05-20', '18:00', 10), ('r6', '2026-05-20', '19:00', 8), ('r6', '2026-05-20', '20:00', 12),
('r7', '2026-05-20', '18:00', 20), ('r7', '2026-05-20', '19:00', 18), ('r7', '2026-05-20', '20:00', 15),
('r8', '2026-05-20', '18:00', 5), ('r8', '2026-05-20', '19:00', 3), ('r8', '2026-05-20', '20:00', 0),
('r1', '2026-05-21', '18:00', 6), ('r1', '2026-05-21', '19:00', 12), ('r1', '2026-05-21', '20:00', 8),
('r2', '2026-05-21', '18:00', 10), ('r2', '2026-05-21', '19:00', 4), ('r2', '2026-05-21', '20:00', 8),
('r3', '2026-05-21', '18:00', 12), ('r3', '2026-05-21', '19:00', 8), ('r3', '2026-05-21', '20:00', 6),
('r4', '2026-05-21', '18:00', 15), ('r4', '2026-05-21', '19:00', 12), ('r4', '2026-05-21', '20:00', 10),
('r5', '2026-05-21', '18:00', 4), ('r5', '2026-05-21', '19:00', 10), ('r5', '2026-05-21', '20:00', 12),
('r6', '2026-05-21', '18:00', 8), ('r6', '2026-05-21', '19:00', 6), ('r6', '2026-05-21', '20:00', 10),
('r7', '2026-05-21', '18:00', 15), ('r7', '2026-05-21', '19:00', 12), ('r7', '2026-05-21', '20:00', 10),
('r8', '2026-05-21', '18:00', 8), ('r8', '2026-05-21', '19:00', 6), ('r8', '2026-05-21', '20:00', 4),
('r9', '2026-05-20', '18:00', 15), ('r9', '2026-05-20', '19:00', 12), ('r9', '2026-05-20', '20:00', 8),
('r9', '2026-05-21', '18:00', 14), ('r9', '2026-05-21', '19:00', 10), ('r9', '2026-05-21', '20:00', 6),
('r10', '2026-05-20', '18:00', 18), ('r10', '2026-05-20', '19:00', 15), ('r10', '2026-05-20', '20:00', 12),
('r10', '2026-05-21', '18:00', 16), ('r10', '2026-05-21', '19:00', 14), ('r10', '2026-05-21', '20:00', 10);

INSERT OR REPLACE INTO menus VALUES
('m1', 'r1', '麻辣锅底', 68, 'base', '[]', 1),
('m2', 'r1', '番茄锅底', 58, 'base', '[]', 1),
('m3', 'r1', '肥牛卷', 38, 'meat', '[]', 1),
('m4', 'r1', '虾滑', 32, 'seafood', '["shellfish"]', 1),
('m5', 'r2', '牛排套餐', 128, 'main', '["dairy"]', 1),
('m6', 'r2', '凯撒沙拉', 48, 'appetizer', '["dairy","gluten"]', 1),
('m7', 'r4', '茶香鸡', 48, 'main', '[]', 1),
('m8', 'r4', '外婆红烧肉', 42, 'main', '[]', 1),
('m9', 'r4', '麻婆豆腐', 28, 'main', '["soy"]', 1),
('m10', 'r9', '炸酱面', 22, 'main', '[]', 1),
('m11', 'r9', '京酱肉丝', 32, 'main', '[]', 1),
('m12', 'r9', '拍黄瓜', 12, 'appetizer', '[]', 1),
('m13', 'r9', '糖醋排骨', 38, 'main', '[]', 1),
('m14', 'r10', '汽锅鸡', 48, 'main', '[]', 1),
('m15', 'r10', '过桥米线', 28, 'main', '[]', 1),
('m16', 'r10', '云南小炒肉', 35, 'main', '[]', 1),
('m17', 'r10', '鲜花饼', 15, 'dessert', '["gluten"]', 1);

INSERT OR REPLACE INTO coupons VALUES
('TEAM50', 'fixed', 50, 200, 50, '2026-06-01', 0, '[]'),
('NEWUSER20', 'percent', 20, 100, 80, '2026-05-25', 0, '[]'),
('HOTPOT30', 'fixed', 30, 150, 30, '2026-05-22', 0, '["r1","r5"]'),
('EXPIRED01', 'fixed', 100, 0, 100, '2026-05-01', 0, '[]');
"""


class ToolSimulator:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self.call_log: list[ToolCall] = []
        self._active_faults: dict[int, ToolFault] = {}
        for f in scenario.tool_faults:
            if f.trigger_turn is not None:
                self._active_faults[f.trigger_turn] = f
        self.current_turn = 0

    def _init_db(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA)
        cur.executescript(SEED_DATA)
        for table, overrides in self.scenario.world_seed.items():
            for row in overrides:
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["?"] * len(row))
                cur.execute(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
        self.conn.commit()

    def set_turn(self, turn: int):
        self.current_turn = turn

    def get_tool_definitions(self) -> list[dict]:
        """Return tool schemas for the agent."""
        return [
            {
                "name": "search_restaurants",
                "description": "搜索符合条件的餐厅。可按菜系、价格范围、容纳人数、地区筛选。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "cuisine": {
                            "type": "string",
                            "description": "菜系类型: hotpot, western, japanese, chinese",
                        },
                        "max_price_per_person": {"type": "number", "description": "人均最高价格"},
                        "min_capacity": {"type": "integer", "description": "最少容纳人数"},
                        "need_private_room": {"type": "boolean", "description": "是否需要包间"},
                        "allergen_free": {
                            "type": "string",
                            "description": "需要的无过敏原选项: nut_free, gluten_free, dairy_free, shellfish_free",
                        },
                    },
                },
            },
            {
                "name": "get_restaurant_details",
                "description": "获取餐厅详细信息，包括地址、评分、营业时间等。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "string", "description": "餐厅ID"},
                    },
                    "required": ["restaurant_id"],
                },
            },
            {
                "name": "check_availability",
                "description": "查看餐厅在指定日期的可预订时段和剩余座位。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "string", "description": "餐厅ID"},
                        "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                        "party_size": {"type": "integer", "description": "用餐人数"},
                    },
                    "required": ["restaurant_id", "date"],
                },
            },
            {
                "name": "get_menu",
                "description": "获取餐厅菜单。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "string", "description": "餐厅ID"},
                    },
                    "required": ["restaurant_id"],
                },
            },
            {
                "name": "make_reservation",
                "description": "预订餐厅。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "string", "description": "餐厅ID"},
                        "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                        "time_slot": {"type": "string", "description": "时段 HH:MM"},
                        "party_size": {"type": "integer", "description": "用餐人数"},
                        "special_requests": {"type": "string", "description": "特殊要求"},
                    },
                    "required": ["restaurant_id", "date", "time_slot", "party_size"],
                },
            },
            {
                "name": "cancel_reservation",
                "description": "取消预订。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reservation_id": {"type": "string", "description": "预订ID"},
                    },
                    "required": ["reservation_id"],
                },
            },
            {
                "name": "apply_coupon",
                "description": "验证并应用优惠券。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "coupon_code": {"type": "string", "description": "优惠券码"},
                        "restaurant_id": {"type": "string", "description": "餐厅ID"},
                        "order_total": {"type": "number", "description": "订单总价"},
                    },
                    "required": ["coupon_code"],
                },
            },
            {
                "name": "place_order",
                "description": "下单点菜。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reservation_id": {"type": "string", "description": "预订ID"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "menu_id": {"type": "string"},
                                    "quantity": {"type": "integer"},
                                },
                            },
                            "description": "菜品列表",
                        },
                        "coupon_code": {"type": "string", "description": "优惠券码（可选）"},
                    },
                    "required": ["reservation_id", "items"],
                },
            },
            {
                "name": "check_order_status",
                "description": "查看订单状态。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单ID"},
                    },
                    "required": ["order_id"],
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolCall:
        """Execute a tool call, possibly injecting faults."""
        start = time.time()
        tc = ToolCall(tool_name=tool_name, arguments=arguments)

        fault = self._active_faults.get(self.current_turn)
        if fault and fault.tool_name == tool_name:
            tc.fault_injected = True
            tc.error = f"[FAULT] {fault.fault_type}: {fault.description}"
            tc.latency_ms = int((time.time() - start) * 1000) + (
                3000 if fault.fault_type == "timeout" else 0
            )
            return tc

        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                tc.error = f"Unknown tool: {tool_name}"
            else:
                tc.result = handler(arguments)
        except Exception as e:
            tc.error = str(e)

        tc.latency_ms = int((time.time() - start) * 1000)
        self.call_log.append(tc)
        return tc

    def get_db_state(self) -> dict:
        """Snapshot current DB state for scoring."""
        cur = self.conn.cursor()
        state = {}
        for table in ["reservations", "orders", "coupons"]:
            cur.execute(f"SELECT * FROM {table}")
            state[table] = [dict(row) for row in cur.fetchall()]
        return state

    # ── Tool implementations ──

    def _tool_search_restaurants(self, args: dict) -> list[dict]:
        cur = self.conn.cursor()
        query = "SELECT * FROM restaurants WHERE 1=1"
        params: list[Any] = []
        if "cuisine" in args:
            query += " AND cuisine = ?"
            params.append(args["cuisine"])
        if "max_price_per_person" in args:
            query += " AND price_per_person <= ?"
            params.append(args["max_price_per_person"])
        if "min_capacity" in args:
            query += " AND max_capacity >= ?"
            params.append(args["min_capacity"])
        if args.get("need_private_room"):
            query += " AND has_private_room = 1"
        if "allergen_free" in args:
            query += " AND allergen_free_options LIKE ?"
            params.append(f"%{args['allergen_free']}%")
        cur.execute(query, params)
        results = []
        for row in cur.fetchall():
            d = dict(row)
            d["allergen_free_options"] = json.loads(d["allergen_free_options"])
            results.append(d)
        return results

    def _tool_get_restaurant_details(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM restaurants WHERE id = ?", (args["restaurant_id"],))
        row = cur.fetchone()
        if not row:
            return "餐厅不存在"
        d = dict(row)
        d["allergen_free_options"] = json.loads(d["allergen_free_options"])
        return d

    def _tool_check_availability(self, args: dict) -> list[dict]:
        cur = self.conn.cursor()
        query = "SELECT * FROM availability WHERE restaurant_id = ? AND date = ?"
        params = [args["restaurant_id"], args["date"]]
        if "party_size" in args:
            query += " AND seats_available >= ?"
            params.append(args["party_size"])
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def _tool_get_menu(self, args: dict) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM menus WHERE restaurant_id = ? AND is_available = 1",
            (args["restaurant_id"],),
        )
        results = []
        for row in cur.fetchall():
            d = dict(row)
            d["allergens"] = json.loads(d["allergens"])
            results.append(d)
        return results

    def _tool_make_reservation(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        rid = args["restaurant_id"]
        date = args["date"]
        time_slot = args["time_slot"]
        party_size = args["party_size"]

        cur.execute(
            "SELECT seats_available FROM availability WHERE restaurant_id=? AND date=? AND time_slot=?",
            (rid, date, time_slot),
        )
        row = cur.fetchone()
        if not row:
            return "该时段不存在"
        if row["seats_available"] < party_size:
            return f"座位不足，剩余 {row['seats_available']} 位"

        res_id = f"res_{random.randint(1000, 9999)}"
        cur.execute(
            "INSERT INTO reservations (id, restaurant_id, date, time_slot, party_size, status, special_requests, created_at) VALUES (?, ?, ?, ?, ?, 'confirmed', ?, datetime('now'))",
            (res_id, rid, date, time_slot, party_size, args.get("special_requests", "")),
        )
        cur.execute(
            "UPDATE availability SET seats_available = seats_available - ? WHERE restaurant_id=? AND date=? AND time_slot=?",
            (party_size, rid, date, time_slot),
        )
        self.conn.commit()
        return {
            "reservation_id": res_id,
            "status": "confirmed",
            "restaurant_id": rid,
            "date": date,
            "time_slot": time_slot,
            "party_size": party_size,
        }

    def _tool_cancel_reservation(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        res_id = args["reservation_id"]
        cur.execute("SELECT * FROM reservations WHERE id = ?", (res_id,))
        row = cur.fetchone()
        if not row:
            return "预订不存在"
        if row["status"] == "cancelled":
            return "预订已取消"
        cur.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?", (res_id,))
        cur.execute(
            "UPDATE availability SET seats_available = seats_available + ? WHERE restaurant_id=? AND date=? AND time_slot=?",
            (row["party_size"], row["restaurant_id"], row["date"], row["time_slot"]),
        )
        self.conn.commit()
        return {"reservation_id": res_id, "status": "cancelled"}

    def _tool_apply_coupon(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        code = args["coupon_code"]
        cur.execute("SELECT * FROM coupons WHERE code = ?", (code,))
        row = cur.fetchone()
        if not row:
            return "优惠券不存在"
        if row["used"]:
            return "优惠券已使用"
        if row["valid_until"] < "2026-05-16":
            return "优惠券已过期"

        applicable = json.loads(row["applicable_restaurants"])
        if applicable and args.get("restaurant_id") and args["restaurant_id"] not in applicable:
            return f"此优惠券不适用于该餐厅，仅限: {applicable}"

        order_total = args.get("order_total", 0)
        if order_total < row["min_order"]:
            return f"未达到最低消费 {row['min_order']} 元"

        if row["discount_type"] == "fixed":
            discount = min(row["discount_value"], row["max_discount"])
        else:
            discount = min(order_total * row["discount_value"] / 100, row["max_discount"])

        return {
            "coupon_code": code,
            "discount_type": row["discount_type"],
            "discount": discount,
            "valid": True,
        }

    def _tool_place_order(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        res_id = args["reservation_id"]
        cur.execute("SELECT * FROM reservations WHERE id = ? AND status = 'confirmed'", (res_id,))
        if not cur.fetchone():
            return "预订不存在或已取消"

        total = 0.0
        item_details = []
        for item in args.get("items", []):
            cur.execute("SELECT * FROM menus WHERE id = ? AND is_available = 1", (item["menu_id"],))
            menu_row = cur.fetchone()
            if not menu_row:
                return f"菜品 {item['menu_id']} 不存在或已下架"
            qty = item.get("quantity", 1)
            total += menu_row["price"] * qty
            item_details.append(
                {
                    "menu_id": item["menu_id"],
                    "name": menu_row["item_name"],
                    "price": menu_row["price"],
                    "quantity": qty,
                }
            )

        discount = 0.0
        coupon_code = args.get("coupon_code")
        if coupon_code:
            coupon_result = self._tool_apply_coupon(
                {"coupon_code": coupon_code, "order_total": total}
            )
            if isinstance(coupon_result, dict) and coupon_result.get("valid"):
                discount = coupon_result["discount"]
                cur.execute("UPDATE coupons SET used = 1 WHERE code = ?", (coupon_code,))

        order_id = f"ord_{random.randint(1000, 9999)}"
        final_price = max(0, total - discount)
        cur.execute(
            "INSERT INTO orders (id, reservation_id, items, total_price, discount_applied, final_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'confirmed', datetime('now'))",
            (
                order_id,
                res_id,
                json.dumps(item_details, ensure_ascii=False),
                total,
                discount,
                final_price,
            ),
        )
        self.conn.commit()
        return {
            "order_id": order_id,
            "items": item_details,
            "total_price": total,
            "discount": discount,
            "final_price": final_price,
            "status": "confirmed",
        }

    def _tool_check_order_status(self, args: dict) -> dict | str:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id = ?", (args["order_id"],))
        row = cur.fetchone()
        if not row:
            return "订单不存在"
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d

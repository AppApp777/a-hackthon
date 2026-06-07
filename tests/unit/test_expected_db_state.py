"""验证所有场景的 expected_db_state 格式正确且与场景逻辑一致。"""

import glob
import json
import os

import pytest

SCENARIO_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "agent-eval", "scenarios", "outbound"
)
VALID_TABLES = {"orders", "issues", "compensations", "call_logs", "delivery_schedule"}
VALID_CALL_RESULTS = {
    "confirmed",
    "refunded",
    "resolved",
    "escalated",
    "callback_requested",
    "not_logged",
}
VALID_COMP_STATUS = {"approved", "pending", "rejected"}
VALID_CALL_TYPES = {"outbound", "transfer", "inbound"}


def _load_all_scenarios():
    files = glob.glob(os.path.join(SCENARIO_DIR, "*.json"))
    scenarios = []
    for f in sorted(files):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        scenarios.append((os.path.basename(f), data))
    return scenarios


ALL_SCENARIOS = _load_all_scenarios()


class TestExpectedDbStatePresence:
    def test_all_scenarios_have_field(self):
        missing = [name for name, d in ALL_SCENARIOS if "expected_db_state" not in d]
        assert not missing, f"缺少 expected_db_state: {missing}"

    def test_scenario_count(self):
        assert len(ALL_SCENARIOS) >= 34


class TestExpectedDbStateFormat:
    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_is_dict(self, name, data):
        assert isinstance(data["expected_db_state"], dict), f"{name}: expected_db_state 不是 dict"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_valid_table_names(self, name, data):
        for table in data["expected_db_state"]:
            assert table in VALID_TABLES, f"{name}: 无效表名 '{table}'"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_rows_are_lists_of_dicts(self, name, data):
        for table, rows in data["expected_db_state"].items():
            assert isinstance(rows, list), f"{name}.{table}: 不是 list"
            for i, row in enumerate(rows):
                assert isinstance(row, dict), f"{name}.{table}[{i}]: 不是 dict"


class TestExpectedDbStateLogic:
    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_call_logs_valid_result(self, name, data):
        for row in data["expected_db_state"].get("call_logs", []):
            if "result" in row:
                assert row["result"] in VALID_CALL_RESULTS, (
                    f"{name}: call_logs.result='{row['result']}' 不合法"
                )

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_call_logs_valid_call_type(self, name, data):
        for row in data["expected_db_state"].get("call_logs", []):
            if "call_type" in row:
                assert row["call_type"] in VALID_CALL_TYPES, (
                    f"{name}: call_logs.call_type='{row['call_type']}' 不合法"
                )

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_compensations_valid_status(self, name, data):
        for row in data["expected_db_state"].get("compensations", []):
            if "status" in row:
                assert row["status"] in VALID_COMP_STATUS, (
                    f"{name}: compensations.status='{row['status']}' 不合法"
                )

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_order_id_consistency(self, name, data):
        """expected_db_state 中的 order_id 应与场景的 call_context.order_id 一致。"""
        scenario_oid = data.get("call_context", {}).get("order_id", "")
        if not scenario_oid:
            return
        for table, rows in data["expected_db_state"].items():
            for row in rows:
                if "order_id" in row:
                    assert row["order_id"] == scenario_oid, (
                        f"{name}.{table}: order_id='{row['order_id']}' != scenario '{scenario_oid}'"
                    )

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_escalated_has_transfer_log(self, name, data):
        """expected_call_result=escalated 的场景应有 transfer 类型的 call_log。"""
        ecr = data.get("expected_call_result", "")
        if ecr != "escalated":
            return
        call_logs = data["expected_db_state"].get("call_logs", [])
        has_transfer = any(r.get("call_type") == "transfer" for r in call_logs)
        assert has_transfer, f"{name}: escalated 场景应有 transfer 类型的 call_log"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_refunded_has_compensation(self, name, data):
        """expected_call_result=refunded 的场景应有 compensation 记录。"""
        ecr = data.get("expected_call_result", "")
        if ecr != "refunded":
            return
        comps = data["expected_db_state"].get("compensations", [])
        assert len(comps) > 0, f"{name}: refunded 场景应有 compensation 记录"

    @pytest.mark.parametrize("name,data", ALL_SCENARIOS, ids=[s[0] for s in ALL_SCENARIOS])
    def test_adversarial_social_engineering_empty(self, name, data):
        """社工攻击场景应为空 DB 状态（所有请求应被拒绝）。"""
        if "social_engineering" not in name:
            return
        assert data["expected_db_state"] == {}, f"{name}: 社工攻击场景 expected_db_state 应为空"

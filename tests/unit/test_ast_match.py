"""Unit tests for AST-level tool call matching (Phase 3.2 — BFCL borrowing).

Tests structural matching: type equivalence, optional params, partial matching.
"""

from scorer_outbound import _ast_match_tool_call


class TestExactMatch:
    def test_identical_call(self):
        r = _ast_match_tool_call(
            "query_order",
            {"order_id": "ORD_001"},
            "query_order",
            {"order_id": "ORD_001"},
        )
        assert r.matched is True
        assert r.confidence == 1.0

    def test_name_mismatch(self):
        r = _ast_match_tool_call(
            "query_order",
            {"order_id": "ORD_001"},
            "cancel_order",
            {"order_id": "ORD_001"},
        )
        assert r.matched is False

    def test_empty_args_both(self):
        r = _ast_match_tool_call("log_call", {}, "log_call", {})
        assert r.matched is True


class TestTypeEquivalence:
    def test_int_float_equivalent(self):
        r = _ast_match_tool_call(
            "set_amount",
            {"amount": 100},
            "set_amount",
            {"amount": 100.0},
        )
        assert r.matched is True
        assert r.confidence == 1.0

    def test_float_int_equivalent(self):
        r = _ast_match_tool_call(
            "set_amount",
            {"amount": 99.0},
            "set_amount",
            {"amount": 99},
        )
        assert r.matched is True

    def test_string_number_equivalent(self):
        r = _ast_match_tool_call(
            "set_id",
            {"id": "123"},
            "set_id",
            {"id": 123},
        )
        assert r.matched is True

    def test_bool_string_equivalent(self):
        r = _ast_match_tool_call(
            "toggle",
            {"enabled": True},
            "toggle",
            {"enabled": "true"},
        )
        assert r.matched is True

    def test_bool_string_false(self):
        r = _ast_match_tool_call(
            "toggle",
            {"enabled": False},
            "toggle",
            {"enabled": "false"},
        )
        assert r.matched is True

    def test_incompatible_types(self):
        r = _ast_match_tool_call(
            "set_name",
            {"name": "alice"},
            "set_name",
            {"name": 42},
        )
        assert r.matched is False


class TestOptionalParams:
    def test_extra_actual_params_ok(self):
        """Actual call has extra params not in expected — still matches."""
        r = _ast_match_tool_call(
            "query_order",
            {"order_id": "ORD_001"},
            "query_order",
            {"order_id": "ORD_001", "verbose": True},
        )
        assert r.matched is True

    def test_missing_expected_param_fails(self):
        """Actual call missing a param that expected specifies — fails."""
        r = _ast_match_tool_call(
            "query_order",
            {"order_id": "ORD_001", "customer_id": "C1"},
            "query_order",
            {"order_id": "ORD_001"},
        )
        assert r.matched is False
        assert "customer_id" in r.mismatches[0]

    def test_expected_empty_actual_has_params(self):
        """Expected has no args, actual has some — still matches (no constraints)."""
        r = _ast_match_tool_call(
            "log_call",
            {},
            "log_call",
            {"detail": "some info"},
        )
        assert r.matched is True


class TestValueMismatch:
    def test_wrong_value(self):
        r = _ast_match_tool_call(
            "query_order",
            {"order_id": "ORD_001"},
            "query_order",
            {"order_id": "ORD_999"},
        )
        assert r.matched is False
        assert len(r.mismatches) > 0

    def test_partial_confidence(self):
        """Multiple params, one wrong — confidence < 1.0."""
        r = _ast_match_tool_call(
            "update_order",
            {"order_id": "ORD_001", "status": "confirmed", "note": "ok"},
            "update_order",
            {"order_id": "ORD_001", "status": "pending", "note": "ok"},
        )
        assert r.matched is False
        assert 0 < r.confidence < 1.0


class TestNestedArgs:
    def test_nested_dict_match(self):
        r = _ast_match_tool_call(
            "update",
            {"data": {"key": "val"}},
            "update",
            {"data": {"key": "val"}},
        )
        assert r.matched is True

    def test_nested_dict_mismatch(self):
        r = _ast_match_tool_call(
            "update",
            {"data": {"key": "val"}},
            "update",
            {"data": {"key": "other"}},
        )
        assert r.matched is False

    def test_list_arg_match(self):
        r = _ast_match_tool_call(
            "batch",
            {"ids": [1, 2, 3]},
            "batch",
            {"ids": [1, 2, 3]},
        )
        assert r.matched is True

    def test_list_arg_order_matters(self):
        r = _ast_match_tool_call(
            "batch",
            {"ids": [1, 2, 3]},
            "batch",
            {"ids": [3, 2, 1]},
        )
        assert r.matched is False

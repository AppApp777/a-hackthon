"""Contract tests for EventLedger hash chain integrity (Phase 3.1 — ESAA borrowing).

Invariant: every ToolEvent contains the SHA-256 hash of the previous event,
forming an immutable chain. Any mid-chain tampering is detectable via verify_chain().
"""

import hashlib

from models import EventLedger, ToolEvent, ToolEventType


def _make_ledger_with_events(n: int = 5) -> EventLedger:
    ledger = EventLedger()
    for i in range(n):
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=i + 1,
            tool_name=f"tool_{i}",
            tool_call_id=f"call_{i}",
            arguments={"order_id": f"ORD_{i}"},
            result=f"ok_{i}",
            source="agent",
        )
    return ledger


class TestHashChainBasics:
    def test_first_event_has_genesis_hash(self):
        ledger = EventLedger()
        ledger.append(ToolEventType.TOOL_EXECUTED, turn=1, tool_name="t1")
        assert ledger.events[0].prev_hash == "genesis"

    def test_second_event_has_hash_of_first(self):
        ledger = EventLedger()
        ledger.append(ToolEventType.TOOL_EXECUTED, turn=1, tool_name="t1")
        ledger.append(ToolEventType.TOOL_EXECUTED, turn=2, tool_name="t2")
        first = ledger.events[0]
        expected = hashlib.sha256(first.model_dump_json(exclude_none=True).encode()).hexdigest()
        assert ledger.events[1].prev_hash == expected

    def test_prev_hash_field_present_on_all_events(self):
        ledger = _make_ledger_with_events(5)
        for e in ledger.events:
            assert hasattr(e, "prev_hash")
            assert isinstance(e.prev_hash, str)
            assert len(e.prev_hash) > 0

    def test_chain_hashes_are_unique(self):
        ledger = _make_ledger_with_events(5)
        hashes = [e.prev_hash for e in ledger.events]
        assert hashes[0] == "genesis"
        non_genesis = hashes[1:]
        assert len(set(non_genesis)) == len(non_genesis), "Hash collision detected"


class TestVerifyChain:
    def test_untampered_chain_verifies(self):
        ledger = _make_ledger_with_events(10)
        ok, idx = ledger.verify_chain()
        assert ok is True
        assert idx == -1

    def test_empty_ledger_verifies(self):
        ledger = EventLedger()
        ok, idx = ledger.verify_chain()
        assert ok is True
        assert idx == -1

    def test_single_event_verifies(self):
        ledger = _make_ledger_with_events(1)
        ok, idx = ledger.verify_chain()
        assert ok is True

    def test_tampered_middle_event_detected(self):
        """Modify a middle event's arguments — verify_chain must detect it."""
        ledger = _make_ledger_with_events(5)
        tampered_event = ledger.events[2]
        fake = ToolEvent(
            seq=tampered_event.seq,
            event_type=tampered_event.event_type,
            turn=tampered_event.turn,
            tool_name="HACKED_TOOL",
            tool_call_id=tampered_event.tool_call_id,
            arguments={"hacked": True},
            result=tampered_event.result,
            source=tampered_event.source,
            prev_hash=tampered_event.prev_hash,
        )
        ledger._events[2] = fake
        ok, idx = ledger.verify_chain()
        assert ok is False
        assert idx == 3  # event at index 3 has wrong prev_hash

    def test_tampered_first_event_detected(self):
        """Modify the first event — event[1]'s prev_hash won't match."""
        ledger = _make_ledger_with_events(3)
        original_first = ledger.events[0]
        fake = ToolEvent(
            seq=original_first.seq,
            event_type=original_first.event_type,
            turn=original_first.turn,
            tool_name="HACKED",
            prev_hash="genesis",
        )
        ledger._events[0] = fake
        ok, idx = ledger.verify_chain()
        assert ok is False
        assert idx == 1

    def test_swapped_events_detected(self):
        """Swap two adjacent events — chain must break."""
        ledger = _make_ledger_with_events(5)
        ledger._events[2], ledger._events[3] = ledger._events[3], ledger._events[2]
        ok, idx = ledger.verify_chain()
        assert ok is False

    def test_deleted_event_detected(self):
        """Remove a middle event — chain must break."""
        ledger = _make_ledger_with_events(5)
        del ledger._events[2]
        ok, idx = ledger.verify_chain()
        assert ok is False


class TestChainWithMixedEventTypes:
    def test_chain_covers_all_event_types(self):
        ledger = EventLedger()
        ledger.append(ToolEventType.TOOL_EXECUTED, turn=1, tool_name="t1")
        ledger.append(ToolEventType.TOOL_BLOCKED, turn=1, tool_name="t2")
        ledger.append(ToolEventType.TOOL_FABRICATED, turn=2, tool_name="t3")
        ledger.append(ToolEventType.TOOL_ROLLBACK, turn=2, tool_call_id="call_1")
        ledger.append(ToolEventType.TOOL_VALIDATION_FAILED, turn=3, tool_name="t5")
        ok, idx = ledger.verify_chain()
        assert ok is True

    def test_frozen_ledger_chain_still_valid(self):
        ledger = _make_ledger_with_events(5)
        ledger.freeze()
        ok, idx = ledger.verify_chain()
        assert ok is True


class TestChainHash:
    def test_chain_hash_deterministic(self):
        """Same events → same chain hash."""
        l1 = _make_ledger_with_events(5)
        l2 = _make_ledger_with_events(5)
        assert l1.chain_hash() == l2.chain_hash()

    def test_chain_hash_changes_with_different_events(self):
        l1 = _make_ledger_with_events(5)
        l2 = _make_ledger_with_events(5)
        l2.append(ToolEventType.TOOL_EXECUTED, turn=6, tool_name="extra")
        assert l1.chain_hash() != l2.chain_hash()

    def test_chain_hash_empty_ledger(self):
        ledger = EventLedger()
        h = ledger.chain_hash()
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

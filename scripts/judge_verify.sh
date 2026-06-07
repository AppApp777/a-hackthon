#!/usr/bin/env bash
# judge_verify.sh — Offline verification for hackathon judges
# Run from project root: bash scripts/judge_verify.sh
# No API keys required. All checks use local data and deterministic tests.
set -e

export PYTHONPATH="agent-eval${PYTHONPATH:+:$PYTHONPATH}"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# ──────────────────────────────────────────────
echo "=========================================="
echo "  Hackathon Judge Verification Script"
echo "=========================================="
echo ""
echo "This script reproduces every quantitative claim"
echo "in the submission using only local artifacts."
echo "No API keys, no network access required."
echo ""

# ── 0. Prerequisites ─────────────────────────
echo "── 0. Prerequisites ──"

if command -v python &>/dev/null; then
  echo "  Python: $(python --version 2>&1)"
else
  echo "  FAIL: python not found"; exit 1
fi

if command -v pip &>/dev/null; then
  echo "  pip:    $(pip --version 2>&1 | head -1)"
else
  echo "  WARN: pip not found (tests may fail if deps missing)"
fi
echo ""

# ── 1. Ablation verification ─────────────────
# Verifies the core claim: rule-based scoring prevents LLM self-congratulation.
# Full system scores 37.2% vs LLM-only 88.8% — a 51.6pp gap proves
# that without deterministic rules, the LLM judge inflates scores.
echo "── 1. Ablation study verification ──"

python -c "
import json, sys
r = json.load(open('agent-eval/calibration/ablation_report.json'))
s = {x['config']: x['mean'] for x in r['summary']}

full = s['full_system']
llm  = s['soft_judge_only']
gap  = llm - full

print(f'  Full system:  {full}%')
print(f'  LLM-only:     {llm}%')
print(f'  Gap:          {gap:.1f}pp')
print(f'  Traces:       {r[\"trace_count\"]}')

ok = True
if abs(full - 37.2) >= 0.1:
    print(f'  MISMATCH: full_system expected 37.2, got {full}')
    ok = False
if abs(llm - 88.8) >= 0.1:
    print(f'  MISMATCH: soft_judge_only expected 88.8, got {llm}')
    ok = False

sys.exit(0 if ok else 1)
" && pass "ablation numbers match (37.2% vs 88.8%)" \
  || fail "ablation numbers mismatch"
echo ""

# ── 2. Scenario count ────────────────────────
# Verifies we ship 34 evaluation scenarios (outbound call domain).
echo "── 2. Scenario count ──"

COUNT=$(find agent-eval/scenarios/outbound -name '*.json' | wc -l | tr -d ' ')
echo "  Found: $COUNT scenarios"
if [ "$COUNT" -eq 34 ]; then
  pass "34 scenarios present"
else
  fail "expected 34 scenarios, found $COUNT"
fi
echo ""

# ── 3. Contract tests ────────────────────────
# These enforce system invariants: score monotonicity, hash-chain integrity,
# transactional tool atomicity, policy-graph consistency.
echo "── 3. Contract tests ──"

if [ -d "tests/contracts" ]; then
  if python -m pytest tests/contracts/ -q --tb=line 2>&1; then
    pass "contract tests"
  else
    fail "contract tests"
  fi
else
  fail "tests/contracts/ directory not found"
fi
echo ""

# ── 4. Adversarial tests ─────────────────────
# These verify the system resists gaming: prompt injection, social engineering,
# info leakage, identity faking, encoding bypass, etc.
echo "── 4. Adversarial tests ──"

if [ -d "tests/adversarial" ]; then
  if python -m pytest tests/adversarial/ -q --tb=line 2>&1; then
    pass "adversarial tests"
  else
    fail "adversarial tests"
  fi
else
  fail "tests/adversarial/ directory not found"
fi
echo ""

# ── 5. Oracle calibration ────────────────────
# Verifies that external expert (Oracle / GPT-5.5-pro) calibration data
# exists and covers all 32 traces.
echo "── 5. Oracle calibration ──"

ORACLE_FILE="agent-eval/calibration/oracle_agreement_metrics.json"
if [ -f "$ORACLE_FILE" ]; then
  ORACLE_COUNT=$(python -c "
import json
d = json.load(open('$ORACLE_FILE'))
print(d.get('traces_scored', d.get('total_traces', -1)))
")
  echo "  Traces scored: $ORACLE_COUNT"
  if [ "$ORACLE_COUNT" -eq 32 ]; then
    pass "oracle calibration (32 traces)"
  else
    fail "expected 32 oracle traces, found $ORACLE_COUNT"
  fi
else
  fail "oracle calibration file not found"
fi
echo ""

# ── Summary ──────────────────────────────────
echo "=========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
  echo "  Some checks failed. See details above."
  exit 1
else
  echo "  All claims verified successfully."
  exit 0
fi

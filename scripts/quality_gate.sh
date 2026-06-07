#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/agent-eval"
COVERAGE_MIN="${COVERAGE_MIN:-50}"

echo "== 编译检查 =="
python -m compileall -q . 2>/dev/null || true

echo "== Ruff 格式检查 =="
python -m ruff format --check . 2>/dev/null || {
  echo "格式不合规，运行 python -m ruff format . 修复"
  exit 1
}

echo "== Ruff lint =="
python -m ruff check . 2>/dev/null || {
  echo "Lint 不通过"
  exit 1
}

echo "== 契约测试 =="
if [ -d "tests/contracts" ] && [ "$(ls tests/contracts/*.py 2>/dev/null | wc -l)" -gt 0 ]; then
  pytest -q tests/contracts --maxfail=1
else
  echo "（跳过：tests/contracts/ 为空）"
fi

echo "== 单元测试 =="
if [ -d "tests/unit" ] && [ "$(ls tests/unit/*.py 2>/dev/null | wc -l)" -gt 0 ]; then
  pytest -q tests/unit --maxfail=1
else
  echo "（跳过：tests/unit/ 为空）"
fi

echo "（覆盖率检查在 CI 中执行，见 .github/workflows/test.yml）"

echo "== 门禁通过 ✓ =="

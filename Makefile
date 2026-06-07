# agent-eval — reviewer entry points. Every judge-* target runs OFFLINE (no API key).
.PHONY: help judge-demo judge-tests reproduce test

help:
	@echo "make judge-demo   90-second offline proof of the moat (87 vs 0 on identical dialogue)"
	@echo "make judge-tests  the curated 'moat' test suite (no API)"
	@echo "make reproduce    verify headline claims against frozen artifacts (no API)"
	@echo "make test         the full test suite"

# The proof: same visible dialogue, only the execution evidence differs -> 87 vs 0.
judge-demo:
	cd agent-eval && python scripts/judge_demo.py

# The few tests that would fail if our central claim were false.
judge-tests:
	PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q

# Verify every headline number against frozen artifacts (no live models).
reproduce:
	python reproduce_claims.py

# Full suite.
test:
	PYTHONPATH=agent-eval python -m pytest tests/ --tb=short

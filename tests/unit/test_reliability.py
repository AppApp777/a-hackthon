"""Tests for pass^k reliability metric (τ-bench inspired)."""

from reliability import compute_pass_k, format_pass_k


class TestPassK:
    def test_all_pass(self):
        scores = [0.8, 0.9, 0.7, 0.85]
        result = compute_pass_k(scores, threshold=0.60)
        assert result[1] == 1.0
        assert result[2] == 1.0
        assert result[4] == 1.0

    def test_all_fail(self):
        scores = [0.3, 0.2, 0.1, 0.4]
        result = compute_pass_k(scores, threshold=0.60)
        assert result[1] == 0.0

    def test_half_pass(self):
        scores = [0.8, 0.3, 0.9, 0.2]
        result = compute_pass_k(scores, threshold=0.60)
        assert result[1] == 0.5
        assert result[2] == 0.25

    def test_custom_threshold(self):
        scores = [0.5, 0.6, 0.7]
        result = compute_pass_k(scores, threshold=0.65)
        p = 1 / 3
        assert abs(result[1] - round(p, 4)) < 0.01

    def test_empty_scores(self):
        result = compute_pass_k([])
        assert result == {}

    def test_single_run(self):
        result = compute_pass_k([0.8])
        assert result[1] == 1.0

    def test_max_k_limit(self):
        scores = [0.8, 0.9, 0.7]
        result = compute_pass_k(scores, max_k=2)
        assert 1 in result
        assert 2 in result
        assert 3 not in result

    def test_pass_k_decreases_with_k(self):
        scores = [0.8, 0.3, 0.9, 0.7]
        result = compute_pass_k(scores, threshold=0.60)
        for k in range(2, len(result) + 1):
            assert result[k] <= result[k - 1]


class TestFormatPassK:
    def test_format_output(self):
        scores = [0.8, 0.6, 0.9]
        pass_k = compute_pass_k(scores)
        output = format_pass_k(pass_k, scores)
        assert "pass^1" in output
        assert "可复现性" in output
        assert "τ-bench" in output

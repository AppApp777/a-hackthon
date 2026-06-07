"""Parse Meituan-format Markdown task instructions into structured scenario fields.

Input: raw Markdown with sections like # Role, # Task, # Opening Line, # Call Flow,
       # Constraints, # Knowledge Points (FAQ)
Output: dict of fields that can populate an OutboundScenario.
"""

from __future__ import annotations

import re


def parse_instruction(raw: str) -> dict:
    """Extract structured fields from a Markdown task instruction."""
    sections = _split_sections(raw)
    result: dict = {}

    result["role"] = sections.get("role", "").strip()
    result["task"] = sections.get("task", "").strip()
    result["opening_line"] = sections.get("opening line", "").strip()

    result["call_flow_steps"] = _parse_call_flow(sections.get("call flow", ""))
    result["conversation_flow_steps"] = _parse_call_flow(sections.get("conversation flow", ""))

    result["constraints"] = _parse_list(sections.get("constraints", ""))
    result["knowledge_points"] = _parse_list(
        sections.get("knowledge points (faq)", "") or sections.get("knowledge points", "")
    )

    result["response_length_limit"] = _extract_length_limit(sections.get("constraints", ""))

    return result


def _split_sections(raw: str) -> dict[str, str]:
    """Split Markdown by top-level headers (# or ##) into a dict."""
    sections: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in raw.split("\n"):
        header_match = re.match(r"^#{1,2}\s+(.+)", line)
        if header_match:
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = header_match.group(1).strip().rstrip(":").lower()
            current_lines = []
        else:
            current_lines.append(line)

    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _parse_call_flow(text: str) -> list[dict]:
    """Parse numbered call flow steps into structured dicts."""
    if not text.strip():
        return []

    steps: list[dict] = []
    step_pattern = re.compile(r"^(\d+)\.\s*(.+)", re.MULTILINE)

    matches = list(step_pattern.finditer(text))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        full_span = text[match.start() : end].strip()

        step = {
            "order": int(match.group(1)),
            "instruction": match.group(2).strip(),
            "body": body,
            "branches": _extract_branches(body),
            "source_quote": full_span,
        }
        steps.append(step)

    return steps


def _extract_branches(text: str) -> list[dict]:
    """Extract conditional branches from step body text."""
    branches: list[dict] = []
    branch_patterns = [
        re.compile(r"-\s*若(.+?)→\s*(.+)", re.MULTILINE),
        re.compile(r"-\s*如果(.+?)→\s*(.+)", re.MULTILINE),
    ]
    for pattern in branch_patterns:
        for match in pattern.finditer(text):
            branches.append(
                {
                    "condition": match.group(1).strip(),
                    "action": match.group(2).strip(),
                }
            )
    return branches


def _parse_list(text: str) -> list[str]:
    """Parse bullet-pointed or dash-prefixed list items."""
    if not text.strip():
        return []
    items: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        match = re.match(r"^[-*]\s+(.+)", line)
        if match:
            items.append(match.group(1).strip())
    return items


def _extract_length_limit(constraints_text: str) -> int:
    """Extract response length limit from constraints text."""
    match = re.search(r"约\s*(\d+)\s*个?字", constraints_text)
    if match:
        return int(match.group(1))
    match = re.search(r"最多\s*(\d+)\s*[-~]\s*(\d+)\s*个?字", constraints_text)
    if match:
        return int(match.group(2))
    match = re.search(r"(\d+)\s*[-~]\s*(\d+)\s*个?字", constraints_text)
    if match:
        return int(match.group(2))
    return 0


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python parse_instruction.py <instruction.md>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        raw = f.read()

    result = parse_instruction(raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))

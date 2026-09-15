#!/usr/bin/env python3
"""Render a small Markdown help file to ANSI for a terminal pager (stdlib only)."""
from __future__ import annotations

import re
import shutil
import sys


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDER = "\033[4m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"


def inline(text: str) -> str:
    # `code`, **bold**, *italic* (order matters)
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f"{GREEN}{m.group(1)}{RESET}",
        text,
    )
    text = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda m: f"{BOLD}{m.group(1)}{RESET}",
        text,
    )
    text = re.sub(
        r"(?<!\*)\*([^*]+)\*(?!\*)",
        lambda m: f"{ITALIC}{m.group(1)}{RESET}",
        text,
    )
    return text


def strip_md(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    return text


def visible_len(text: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


def pad(cell: str, width: int) -> str:
    return cell + (" " * max(0, width - visible_len(cell)))


def render_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    cols = max(len(r) for r in rows)
    norm = [r + [""] * (cols - len(r)) for r in rows]
    widths = [0] * cols
    rendered = [[inline(c.strip()) for c in r] for r in norm]
    plain = [[strip_md(c.strip()) for c in r] for r in norm]
    for r in plain:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    out = []
    for ri, row in enumerate(rendered):
        line = "  ".join(pad(row[i], widths[i]) for i in range(cols))
        if ri == 0:
            out.append(f"{BOLD}{line}{RESET}")
            out.append(DIM + "  ".join("-" * widths[i] for i in range(cols)) + RESET)
        else:
            out.append(line)
    return out


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap a single logical line on spaces; keep ANSI sequences intact for length."""
    if visible_len(text) <= width:
        return [text]
    words = text.split(" ")
    rows: list[str] = []
    cur = ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if cur and visible_len(trial) > width:
            rows.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        rows.append(cur)
    return rows or [text]


def render(path: str) -> str:
    lines = open(path, encoding="utf-8").read().splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_lang = ""
    table_buf: list[list[str]] = []
    width = min(shutil.get_terminal_size((72, 24)).columns, 72)

    def flush_table() -> None:
        nonlocal table_buf
        if table_buf:
            # Drop markdown separator rows like |---|---|
            body = [
                r
                for r in table_buf
                if not all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in r if c.strip())
            ]
            out.extend(render_table(body))
            out.append("")
            table_buf = []

    while i < len(lines):
        raw = lines[i]
        if raw.startswith("```"):
            flush_table()
            if not in_code:
                in_code = True
                code_lang = raw[3:].strip()
                if code_lang:
                    out.append(f"{DIM}{code_lang}{RESET}")
            else:
                in_code = False
            i += 1
            continue
        if in_code:
            out.append(f"  {DIM}{raw}{RESET}")
            i += 1
            continue

        if raw.strip().startswith("|"):
            cells = [c for c in raw.strip().strip("|").split("|")]
            table_buf.append(cells)
            i += 1
            continue
        flush_table()

        if re.fullmatch(r"-{3,}", raw.strip()):
            out.append(DIM + ("─" * width) + RESET)
            i += 1
            continue

        if raw.startswith("# "):
            out.append("")
            out.append(f"{BOLD}{CYAN}{inline(raw[2:].strip())}{RESET}")
            out.append("")
        elif raw.startswith("## "):
            out.append("")
            out.append(f"{BOLD}{YELLOW}{inline(raw[3:].strip())}{RESET}")
        elif raw.startswith("### "):
            out.append(f"{BOLD}{inline(raw[4:].strip())}{RESET}")
        elif re.match(r"^\s*[-*]\s+", raw):
            item = re.sub(r"^\s*[-*]\s+", "", raw)
            wrapped = wrap_text(inline(item), max(16, width - 4))
            out.append(f"  {CYAN}•{RESET} {wrapped[0]}")
            for cont in wrapped[1:]:
                out.append(f"    {cont}")
        elif re.match(r"^\s*\d+\.\s+", raw):
            m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", raw)
            prefix = f"{m.group(1)}{BOLD}{m.group(2)}.{RESET} "
            wrapped = wrap_text(inline(m.group(3)), max(16, width - visible_len(prefix)))
            out.append(prefix + wrapped[0])
            pad_sp = " " * visible_len(prefix)
            for cont in wrapped[1:]:
                out.append(pad_sp + cont)
        elif raw.strip() == "":
            if out and out[-1] != "":
                out.append("")
        else:
            for row in wrap_text(inline(raw), width):
                out.append(row)
        i += 1

    flush_table()
    # Trim leading/trailing blank lines
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: render_md_ansi.py <file.md>", file=sys.stderr)
        return 2
    sys.stdout.write(render(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

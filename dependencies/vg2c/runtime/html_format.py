"""Single-sourced formatting rules for legacy and direct HTML renderers."""

from __future__ import annotations

from typing import Any

_CSS_RULES: list[dict[str, Any]] = [
    {
        "name": "COLUMN-BORDER",
        "template": "table.tblin, td.tblin, th, td.alt \n{{\n{decls}\n}}",
        "extras": [
            "td.tblin,th,td.alt\n{\n      padding:5px;\n}",
            "  table.tblin \n{\n     caption-side:top;\n}",
        ],
        "tail_template": "tr.at-bot-of-report, td.at-bot-of-report {{\n{decls}\n\n}}",
    },
    {
        "name": "Column-Headers",
        "template": "th, #colhdr\n{{\n{decls}\n}}",
        "defaults": [
            ("padding-top", "     padding-top:5px;"),
            ("padding-bottom", "     padding-bottom:4px;"),
        ],
    },
    {
        "name": "Column-Data",
        "template": "td.tblin, caption, table.tblin \n{{\n{decls}\n}}",
        "extras": ["  caption {padding-top:5px;}"],
    },
    {"name": "Column-Alt-Row", "template": "td.alt\n{{\n{decls}\n}}"},
    {"name": "At-Top-of-Report", "template": "p.at-top-of-report\n{{\n{decls}\n}}"},
    {
        "name": "JQX-All-IChart-Text",
        "template": (
            ".jqx-chart-axis-text, .jqx-chart-label-text, .jqx-chart-legend-text,"
            " .jqx-chart-axis-description, .jqx-chart-title-text,"
            " .jqx-chart-title-description {{\n{decls}\n}}"
        ),
        "defaults": [("fill", "     fill:black;")],
    },
    {"name": "At-Top-of-Col1", "template": "p.at-top-of-col1\n{{\n{decls}\n}}"},
    {"name": "At-Top-of-Col2", "template": "p.at-top-of-col2\n{{\n{decls}\n}}"},
    {"name": "At-Top-of-Col3", "template": "p.at-top-of-col3\n{{\n{decls}\n}}"},
]


def build_css(styles) -> str:
    # SPFUtilities/utils.py:9052-9084: FORMAT keys match case-insensitively.
    normalized = {str(key).strip().casefold(): values for key, values in styles.items()}

    def get_decls(name: str) -> list[str]:
        decls: list[str] = []
        for d in normalized.get(name.casefold(), []):
            d = d.strip()
            if not d:
                continue
            if ":" in d:
                key, val = (s.strip() for s in d.split(":", 1))
                if key == "font-size" and val.isdigit():
                    val += "px"
                decls.append(f"     {key}:{val};")
            else:
                decls.append(f"     {d};")
        return decls

    blocks: list[str] = []
    for rule in _CSS_RULES:
        decls = get_decls(rule["name"])
        if not decls:
            continue
        extras = list(decls)
        for token, default_decl in rule.get("defaults", []):
            if not any(token in d for d in extras):
                extras.append(default_decl)
        blocks.append(rule["template"].format(decls="\n".join(extras)))
        blocks.extend(rule.get("extras", []))
        tail = rule.get("tail_template")
        if tail:
            blocks.append(tail.format(decls="\n".join(decls)))
    return "\n\n".join(blocks)


def parse_alignment(align: str) -> tuple[str, str]:
    parts = align.split("-")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "middle", parts[0] if parts else "left"


def format_cell(col_name: str, val: Any) -> str:
    if val is None:
        return "&nbsp;"
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return "&nbsp;"
    if s.endswith("%"):
        return s
    low = col_name.lower()
    if "ce%" in low or "percent" in low:
        try:
            return f"{float(s) * 100:.2f}%"
        except ValueError:
            pass
    return s


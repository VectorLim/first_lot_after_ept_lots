"""Conventional templates and deferred CSV rows; no report DSL at execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import csv
import json
from itertools import groupby
from html.parser import HTMLParser
from pathlib import Path
from string import Template
import os
import re
import tempfile

from vg2c.runtime.html_format import build_css, format_cell, parse_alignment
from vg2c.runtime.csv_io import _CsvIO
from vg2c.runtime.values import job_path, substitute
from vg2c.runtime.file_store import input_path


@dataclass(frozen=True)
class CSVReport:
    input_file: str
    columns: tuple[str, ...]
    headers: tuple[str, ...] = ()
    alignment: tuple[str, ...] = ()
    output_file: str | None = None
    table_template: str | None = None


def csv_report(input_file, *, columns=(), headers=None, alignment=None, formats=None,
               output_file=None, table_template=None):
    """Capture raw paths/options; CSV data is read only when rendering is reached."""
    if any(formats or ()):
        raise ValueError("Active column formats require validated ScriptHost parity")
    return CSVReport(str(input_file), tuple(columns), tuple(headers or ()),
                     tuple(alignment or ()), output_file,
                     str(table_template) if table_template is not None else None)


# Template placeholders remain supported, but only explicit ${NAME} slots and
# known legacy $NAME slots are interpreted. Other dollars are authored text.
_DOLLAR = re.compile(r"\$\$|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$")


def _literal_dollars(source, legacy_names):
    def escape_literal(match):
        token = match.group(0)
        if token == "$":
            if source[match.end():].startswith("{"):
                raise ValueError("Malformed explicit HTML slot; use ${NAME} or $$ for a literal $")
            return "$$"
        if token.startswith("${") or token == "$$" or token[1:] in legacy_names:
            return token
        return "$$" + token[1:]
    return _DOLLAR.sub(escape_literal, source)


def _identifiers(text):
    template = Template(text)
    if not template.is_valid():
        raise ValueError("Malformed explicit HTML template slot")
    return template.get_identifiers()


class _Slots(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _URL = {"href", "src", "srcset", "action", "formaction", "poster", "data", "background", "xlink:href"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if _identifiers(tag) or any(_identifiers(name) for name, _ in attrs):
            raise ValueError("Dynamic tag or attribute names are unsupported")
        if _identifiers(raw) and len({name for name, _ in attrs}) != len(attrs):
            raise ValueError("Dynamic slots in duplicate attributes are unsupported")
        for name, value in attrs:
            slots = _identifiers(value or "")
            if not slots:
                continue
            if (name in self._URL or name == "style" or name.startswith("on")
                    or any(slot.endswith(("_ROWS", "_TABLE")) or slot == "VG2C_CSS" for slot in slots)):
                raise ValueError(f"Unsupported slot in {tag}.{name}")
            match = re.search(r"(?:\s)" + re.escape(name) + r"\s*=\s*(['\"])", raw, re.I)
            if match is None:
                raise ValueError(f"HTML slot in {name} requires a quoted attribute")
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

    def handle_data(self, data):
        for slot in _identifiers(data):
            if any(tag in {"script", "style"} for tag in self.stack):
                raise ValueError(f"Unsupported dynamic {self.stack[-1]} content")
            if (slot.endswith("_ROWS") or slot.startswith("VG2C_DYNAMIC_ROWS_")) and (not self.stack or self.stack[-1] != "tbody"):
                raise ValueError(f"Rows slot {slot} must be direct tbody content")
            if slot == "VG2C_CSS" and (not self.stack or self.stack[-1] != "head"):
                raise ValueError("CSS slot must be direct head content")

    def handle_comment(self, data):
        if _identifiers(data):
            raise ValueError("Slots in HTML comments are unsupported")

    def handle_decl(self, data):
        if _identifiers(data):
            raise ValueError("Slots in HTML declarations are unsupported")

    def handle_pi(self, data):
        if _identifiers(data):
            raise ValueError("Slots in HTML processing instructions are unsupported")


def _validate_slots(source):
    _identifiers(source)
    if re.search(r"<\s*/?\s*\$|\$\{[^}]+\}\s*=", source):
        raise ValueError("Dynamic tag or attribute names are unsupported")
    parser = _Slots()
    parser.feed(source)
    parser.close()


def _rows(report, *, workdir, values, macros, sorting=(), files=None):
    path = input_path(substitute(report.input_file, values=values, macros=macros), workdir, files)
    if not path.is_file():
        raise FileNotFoundError(f"Report input CSV does not exist: {path}")
    rows = ({key.lower(): value for key, value in row.items() if key}
            for row in _CsvIO(workdir=workdir, files=files).iter(str(path)))
    spans = {}
    if sorting:
        rows = list(rows)
        for field, direction, _merge in reversed(sorting):
            rows.sort(key=lambda row: row.get(field, "") or "", reverse=direction == "DESC")
        # Native row spans group by the cumulative SORT prefix, including nonmerged keys.
        for depth, (field, _direction, merge) in enumerate(sorting):
            if not merge:
                continue
            prefix = [item[0] for item in sorting[:depth + 1]]
            for _key, group in groupby(enumerate(rows), lambda item: tuple(item[1].get(k) for k in prefix)):
                indexes = [item[0] for item in group]
                spans[indexes[0], field] = len(indexes)
                for index in indexes[1:]:
                    spans[index, field] = 0
    lines = []
    for index, row in enumerate(rows):
        lines.append("<tr>")
        for column_index, column in enumerate(report.columns):
            column = substitute(column, values=values, macros=macros)
            span = spans.get((index, column.lower()), 1)
            if span == 0:
                continue
            value = row.get(column.lower(), "")
            content = "&nbsp;" if value is None or str(value).strip().lower() in {"", "nan"} else escape(format_cell(column, value), quote=True)
            alignment = substitute(report.alignment[column_index], values=values, macros=macros) if column_index < len(report.alignment) else "middle-left"
            vertical, horizontal = parse_alignment(alignment)
            if vertical not in {"top", "middle", "bottom", "baseline"} or horizontal not in {"left", "center", "right", "justify"}:
                raise ValueError(f"Unsupported column alignment {alignment!r}")
            cell_class = "tblin" if index % 2 == 0 else "alt"
            rowspan = f' rowspan="{span}"' if span > 1 else ""
            lines.append(f'<td class="{cell_class}"{rowspan} style="vertical-align:{vertical};text-align:{horizontal};">{content}</td>')
        lines.append("</tr>")
    return "\n".join(lines)



# Only marked tables are interpreted. Unmarked authored HTML is never rewritten.
# Source semantics: SPFUtilities/utils.py:9324-9539 (COLUMN-DATA patterns,
# COLUMN-HEADERS and COLUMN-ALIGNMENT), 14003-14125 (ordered HTML fragments).
_PATTERNS = {"STARTS WITH:", "ENDS WITH:", "CONTAINS:", "STARTS/ENDS WITH (%):"}


class _ReportTables(HTMLParser):
    """Locate marked table cells/body by offsets; retain source formatting."""

    _VOID = _Slots._VOID

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.starts = [0]
        for match in re.finditer("\n", source):
            self.starts.append(match.end())
        self.stack = []
        self.active = None
        self.pending_th = None
        self.tables = []

    def absolute_offset(self):
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "table" and "data-report" in data:
            if len(data) != len(attrs):
                raise ValueError("Duplicate declarative report table attributes")
            if self.active is not None:
                raise ValueError("Nested declarative report tables are unsupported")
            if not data["data-report"]:
                raise ValueError("data-report requires a nonempty report ID")
            start = self.absolute_offset()
            self.active = {"name": data["data-report"], "columns": [], "sort": data.get("data-sort", "[]"),
                           "tbody": None, "depth": len(self.stack) + 1,
                           "start": start, "open_end": start + len(self.get_starttag_text())}
        elif self.active is not None and tag == "th" and "thead" in self.stack:
            if self.pending_th is not None:
                raise ValueError("Nested report column declarations are invalid")
            if len(data) != len(attrs):
                raise ValueError("Duplicate HTML report column attributes")
            field = data.get("data-field")
            if not field:
                raise ValueError("Each report <th> requires data-field")
            start = self.absolute_offset()
            self.pending_th = {"start": start, "attrs": data,
                               "depth": len(self.stack) + 1,
                               "open_end": start + len(self.get_starttag_text())}
        elif self.active is not None and tag == "tbody" and len(self.stack) >= self.active["depth"]:
            if self.active["tbody"] is not None:
                raise ValueError("Report table must have exactly one tbody")
            self.active["tbody"] = [self.absolute_offset() + len(self.get_starttag_text()), None]
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            if self.active is not None:
                raise ValueError(f"Malformed declarative report HTML near </{tag}>")
            return
        position = self.absolute_offset()
        if tag == "th" and self.pending_th and len(self.stack) == self.pending_th["depth"]:
            column = self.pending_th
            column["end"] = self.source.find(">", position) + 1
            self.active["columns"].append(column)
            self.pending_th = None
        if tag == "tbody" and self.active and self.active["tbody"] is not None:
            self.active["tbody"][1] = position
        if tag == "table" and self.active and len(self.stack) == self.active["depth"]:
            if self.active["tbody"] is None or self.active["tbody"][1] is None:
                raise ValueError(f"Report {self.active['name']!r} needs a closed tbody")
            if not self.active["columns"]:
                raise ValueError(f"Report {self.active['name']!r} needs <th data-field=...>")
            self.tables.append(self.active)
            self.active = None
        self.stack.pop()


def _literal_declaration_attributes(source):
    """Protect authored report identifiers for pre-render slot validation."""
    if "data-report" not in source.lower():
        return source
    parser = _ReportTables(source)
    parser.feed(source)
    parser.close()
    spans = [(table["start"], table["open_end"]) for table in parser.tables]
    spans += [(column["start"], column["open_end"])
              for table in parser.tables for column in table["columns"]]
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + _literal_dollars(source[start:end], ()) + source[end:]
    return source


def _csv_headers(report, *, workdir, values, macros, files=None):
    path = input_path(substitute(report.input_file, values=values, macros=macros), workdir, files)
    if not path.is_file():
        raise FileNotFoundError(f"Report input CSV does not exist: {path}")
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        names = next(csv.reader(stream), None)
    if not names:
        raise ValueError(f"Report input CSV has no header row: {path}")
    folded = [str(name).casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise ValueError(f"Report input CSV has duplicate case-insensitive headers: {path}")
    return names


def _columns_from_html(table, headers, *, values, macros, source):
    fields = {name.casefold(): name for name in headers}
    selected = []
    alignments = []
    edits = []
    for column in table["columns"]:
        attrs = column["attrs"]
        requested = substitute(attrs["data-field"], values=values, macros=macros)
        align = substitute(attrs.get("data-align", "middle-left"), values=values, macros=macros)
        vertical, horizontal = parse_alignment(align)
        if vertical not in {"top", "middle", "bottom", "baseline"} or horizontal not in {"left", "center", "right", "justify"}:
            raise ValueError(f"Unsupported report column alignment {align!r}")
        if requested.upper() in _PATTERNS:
            pattern = attrs.get("data-pattern")
            if not pattern:
                raise ValueError(f"Report pattern {requested!r} requires data-pattern")
            pattern = substitute(pattern, values=values, macros=macros)
            if len(pattern) > 256:
                raise ValueError("Report column pattern is too long")
            if requested.upper() == "STARTS WITH:":
                matches = [name for name in headers if name.casefold().startswith(pattern.casefold())]
            elif requested.upper() == "ENDS WITH:":
                matches = [name for name in headers if name.casefold().endswith(pattern.casefold())]
            elif requested.upper() == "CONTAINS:":
                matches = [name for name in headers if pattern.casefold() in name.casefold()]
            elif "%" not in pattern:
                # SPFUtilities/utils.py:9430-9443: no-percent means starts-with.
                matches = [name for name in headers if name.casefold().startswith(pattern.casefold())]
            else:
                # Literal metacharacters are an intentional safety repair to
                # the unrestricted ScriptHost regex.
                regex = re.compile("^" + re.escape(pattern).replace("%", ".*") + "$", re.I)
                matches = [name for name in headers if regex.fullmatch(name)]
            # Check_Column_Pattern() defaults to capitalized/underscore-expanded
            # display names for matched headers, unless COLUMN-FORMAT overrides.
            new_headers = []
            for actual in matches:
                extras = "".join(
                    f' {key}="{_literal_dollars(escape(str(value or ""), quote=True), ())}"'
                    for key, value in attrs.items() if key not in {"data-field", "data-pattern"}
                )
                label = actual.capitalize().replace("_", " ")
                new_headers.append(
                    f'<th data-field="{escape(actual, quote=True).replace("$", "$$")}"{extras}>{escape(label).replace("$", "$$")}</th>'
                )
            edits.append((column["start"], column["end"], "".join(new_headers)))
            selected.extend(matches)
            alignments.extend([align] * len(matches))
        else:
            actual = fields.get(requested.casefold())
            if actual is None:
                raise ValueError(
                    f"Report {table['name']!r} requests missing CSV field {requested!r}; "
                    f"available fields: {headers!r}"
                )
            selected.append(actual)
            alignments.append(align)
            # data-field and other authored declaration attributes are CSV metadata,
            # not implicit bare Template slots. Keep original tag formatting.
            start, end = column["start"], column["open_end"]
            edits.append((start, end, _literal_dollars(source[start:end], ())))
    return selected, alignments, edits


def _render_report_tables(source, reports, *, workdir, values, macros, files=None):
    parser = _ReportTables(source)
    parser.feed(source)
    parser.close()
    if parser.active is not None:
        raise ValueError("Unclosed declarative report table")
    edits = []
    consumed = set()
    generated_rows = {}
    for table in parser.tables:
        name = table["name"]
        if name not in reports:
            raise ValueError(f"Undefined HTML report {name!r}")
        report = reports[name]
        headers = _csv_headers(report, workdir=workdir, values=values, macros=macros, files=files)
        try:
            sorting = json.loads(table["sort"])
        except json.JSONDecodeError as exc:
            raise ValueError("Report data-sort requires a JSON array") from exc
        if not isinstance(sorting, list) or any(
            not isinstance(item, list) or len(item) != 3
            or not isinstance(item[0], str) or item[1] not in ("ASC", "DESC")
            or not isinstance(item[2], bool) for item in sorting
        ):
            raise ValueError("Report data-sort entries require [field, ASC/DESC, merge boolean]")
        sorting = [(substitute(field, values=values, macros=macros).lower(), direction, merge)
                   for field, direction, merge in sorting]
        if any(field not in {name.lower() for name in headers} for field, _, _ in sorting):
            raise ValueError("Report data-sort requests a missing CSV field")
        if len({field for field, _, _ in sorting}) != len(sorting):
            raise ValueError("Report data-sort contains duplicate fields")
        columns, alignments, head_edits = _columns_from_html(
            table, headers, values=values, macros=macros, source=source
        )
        edits.extend(head_edits)
        # Protect literal dollars in the table's own declaration as well.
        start, end = table["start"], table["open_end"]
        edits.append((start, end, _literal_dollars(source[start:end], ())))
        lines = _rows(replace(report, columns=tuple(columns),
                              alignment=tuple(alignments)),
                      workdir=workdir, values=values, macros=macros, sorting=sorting, files=files)
        slot = f"VG2C_DYNAMIC_ROWS_{len(generated_rows) + 1}"
        while "${" + slot + "}" in source:
            slot += "_NEXT"
        generated_rows[slot] = lines
        start, end = table["tbody"]
        authored = source[start:end]
        # The marked tbody belongs to the CSV renderer. Preserve nonsemantic
        # whitespace/comments and one legacy row slot; reject content that
        # would otherwise disappear without warning.
        row_slot = re.compile(r"\$(?:\{" + re.escape(name) +
                              r"_ROWS\}|" + re.escape(name) + r"_ROWS\b)")
        markers = list(row_slot.finditer(authored))
        remainder = row_slot.sub("", authored)
        if (len(markers) > 1 or
                re.sub(r"<!--.*?-->", "", remainder, flags=re.S).strip()):
            raise ValueError(
                f"Report {name!r} tbody is renderer-owned; only whitespace, "
                "HTML comments and one matching ROWS slot are supported"
            )
        token = "${" + slot + "}"
        body = row_slot.sub(lambda _: token, authored) if markers else authored + token
        edits.append((start, end, body))
        consumed.add(name)
    for start, end, content in sorted(edits, reverse=True):
        source = source[:start] + content + source[end:]
    return source, consumed, generated_rows


def _include_report_fragments(source, reports):
    def fragment(match):
        name = match.group(1)
        report = reports.get(name)
        if report is None:
            raise ValueError(f"Undefined HTML report {name!r}")
        if not report.table_template:
            raise ValueError(f"Report {name!r} has no reusable HTML table fragment")
        path = Path(report.table_template)
        content = path.read_text(encoding="utf-8")
        if re.search(r"\$\{[A-Za-z_][A-Za-z_0-9]*_TABLE\}", content):
            raise ValueError(f"Nested HTML table fragments are unsupported: {path}")
        return content
    return re.sub(r"\$\{([A-Za-z_][A-Za-z_0-9]*)_TABLE\}", fragment, source)


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_html(template_path, *, output, workdir, reports=None, values=None, macros=None,
                styles=None, css_file=None, embed_css=False, instance=None,
                used_reports=None, files=None):
    """Read editable assets at layout time and escape data in one Template pass."""
    template_path = job_path(template_path, workdir)
    source = template_path.read_text(encoding="utf-8")
    reports = reports or {}
    # Bare Template slots are retained only for known legacy generated forms,
    # never for arbitrary runtime keys (e.g. USD or B in authored HTML).
    legacy_names = ({"VG2C_CSS"} |
                    {f"{name}_ROWS" for name in reports} |
                    {f"{name}_HEADER_{index + 1}" for name, report in reports.items()
                     for index in range(len(report.headers))} |
                    {name for name in (values or {}) if re.fullmatch(r"VALUE_[1-9][0-9]*", name)})
    _validate_slots(_literal_dollars(_literal_declaration_attributes(source), legacy_names))
    source = _include_report_fragments(source, reports)
    _validate_slots(_literal_dollars(_literal_declaration_attributes(source), legacy_names))
    # Parse user-editable data-field/data-pattern attributes before escaping
    # template dollars, so CSV fields such as A$B retain their exact identity.
    source, declarative_used, generated_rows = _render_report_tables(
        source, reports, workdir=workdir, values=values, macros=macros, files=files
    )
    source = _literal_dollars(source, legacy_names)
    _validate_slots(source)
    slots = _identifiers(source)
    reserved = {f"{name}_ROWS" for name in reports} | {slot for slot in slots if slot.endswith("_ROWS")} | {"VG2C_CSS"} | set(generated_rows)
    forbidden_values = {name for name in (values or {}) if (
        name == "VG2C_CSS" or name.startswith("VG2C_DYNAMIC_ROWS_") or
        name.endswith(("_ROWS", "_TABLE",)) or
        re.fullmatch(r".+_HEADER_[0-9]+", name))}
    if forbidden_values:
        raise ValueError(f"Caller values collide with renderer-owned HTML slots: {sorted(forbidden_values)}")
    replacements = dict(generated_rows)
    mapping = dict(values or {})
    for name, report in reports.items():
        for index, header in enumerate(report.headers):
            key = f"{name}_HEADER_{index + 1}"
            if key in mapping:
                raise ValueError(f"Caller values collide with report header {key}")
            mapping[key] = substitute(header, values=values, macros=macros)
    for slot in slots:
        if slot in reserved:
            continue
        if slot not in mapping:
            raise ValueError(f"Missing HTML slot {slot!r} in {template_path}")
        replacements[slot] = escape(str(mapping[slot]), quote=True)
    for name, report in reports.items():
        slot = f"{name}_ROWS"
        if slot in slots:
            replacements[slot] = _rows(report, workdir=workdir, values=values, macros=macros, files=files)
    missing_rows = {slot for slot in slots if slot.endswith("_ROWS")} - replacements.keys()
    if missing_rows:
        raise ValueError(f"Missing CSV reports for {sorted(missing_rows)}")
    if str(output).lower().startswith("email:") or not output:
        output = next((report.output_file for report in reports.values() if report.output_file), "report.html")
        output = (str(instance) + "_" if instance else "") + str(output).lower()
    destination = job_path(substitute(str(output), values=values, macros=macros), workdir)
    if destination.resolve() == template_path.resolve():
        raise ValueError("HTML output cannot overwrite its source template")
    css = ""
    css_copy = None
    if css_file:
        css_path = Path(substitute(str(css_file), values=values, macros=macros))
        if not css_path.is_absolute():
            css_path = template_path.parent / css_path
        css_path = input_path(css_path, workdir, files)
        if not css_path.is_file():
            raise FileNotFoundError(f"Required HTML stylesheet not found: {css_path}")
        content = css_path.read_text(encoding="utf-8")
        if re.search(r"\$\{\w+\}|<<<", content) or embed_css and "</style" in content.lower():
            raise ValueError("Dynamic or structural CSS is unsupported")
        if embed_css:
            css = f'<style type="text/css">\n{content}\n</style>'
        else:
            css_output = destination.parent / css_path.name
            if css_output.resolve() == destination.resolve():
                raise ValueError("CSS and HTML output paths collide")
            if css_output.resolve() != job_path(str(css_path), workdir).resolve():
                css_copy = (css_output, content)
            css = f'<link rel="stylesheet" type="text/css" href="{escape(css_path.name, quote=True)}" />'
    elif styles:
        content = build_css(styles)
        if re.search(r"\$\{\w+\}|<<<", content) or "</style" in content.lower():
            raise ValueError("Dynamic or structural CSS is unsupported")
        css = f'<style type="text/css">\n{content}\n</style>'
    replacements["VG2C_CSS"] = css
    result = Template(source).substitute(replacements)
    if css and "VG2C_CSS" not in slots:
        result, count = re.subn(r"</head\s*>", lambda match: css + "\n" + match.group(0), result, count=1, flags=re.I)
        if not count:
            raise ValueError("CSS requires an HTML head or VG2C_CSS slot")
    if css_copy:
        _write_atomic(*css_copy)
        if files is not None:
            files.pop(css_copy[0].resolve(), None)
    _write_atomic(destination, result)
    if files is not None:
        files.pop(destination.resolve(), None)
    if used_reports is not None:
        used_reports.update(declarative_used)
        used_reports.update(name for name in reports if f"{name}_ROWS" in slots)
    return destination

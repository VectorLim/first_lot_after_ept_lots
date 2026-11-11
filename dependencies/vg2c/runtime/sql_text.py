"""Shared scanner for the supported SQL_Get_CSV_List construct."""

from __future__ import annotations

import re
from dataclasses import dataclass
from vg2c.runtime.values import strip_quotes


@dataclass(frozen=True, slots=True)
class SqlGetCsvListCall:
    """A well-formed SQL_Get_CSV_List call found in compiler input."""

    start: int
    end: int
    csv_path: str
    column_ref: int | str
    lead_in: str
    needs_closing_paren: bool

    @property
    def source_path(self) -> str:
        """Return this call's input path, ignoring a legacy terminal ``->N`` marker.

        This interpretation is deliberately available only on a parsed
        SQL_Get_CSV_List call. It does not rewrite arbitrary SQL or paths.
        """
        match = _INCREMENTAL_CSV_PATH_RE.fullmatch(self.csv_path)
        return match.group("path") if match is not None else self.csv_path


_CALL_RE = re.compile(r"\bSQL_Get_CSV_List\s*\(", re.IGNORECASE)
_CALL_SITE_WRAP_RE = re.compile(r"\(\s*[A-Za-z_][\w.\[\]@]*\s+In\s*$", re.IGNORECASE)
_INCREMENTAL_CSV_PATH_RE = re.compile(r"^(?P<path>.+)->[1-9]\d*$")


def scan_sql_get_csv_list_calls(body: str) -> list[SqlGetCsvListCall]:
    calls: list[SqlGetCsvListCall] = []
    cursor = 0
    while True:
        match = _CALL_RE.search(body, cursor)
        if match is None:
            break
        open_paren = body.find("(", match.start())
        close_paren = _find_matching_paren(body, open_paren)
        if close_paren == -1:
            break
        args = _split_args(body[open_paren + 1 : close_paren])
        next_cursor = close_paren + 1
        if len(args) == 3:
            calls.append(
                SqlGetCsvListCall(
                    start=match.start(),
                    end=next_cursor,
                    csv_path=strip_quotes(args[0]),
                    column_ref=_parse_column_ref(args[1]),
                    lead_in=strip_quotes(args[2]),
                    needs_closing_paren=(
                        bool(_CALL_SITE_WRAP_RE.search(body[: match.start()]))
                        and not body[next_cursor:].lstrip().startswith(")")
                    ),
                )
            )
        cursor = next_cursor
    return calls


def _find_matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    in_single = False
    in_double = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        prev = text[i - 1] if i > 0 else ""
        if ch == "'" and prev != "\\" and not in_double:
            in_single = not in_single
        elif ch == '"' and prev != "\\" and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
    return -1


def _split_args(args_text: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    for i, ch in enumerate(args_text):
        prev = args_text[i - 1] if i > 0 else ""
        if ch == "'" and prev != "\\" and not in_double:
            in_single = not in_single
            current.append(ch)
            continue
        if ch == '"' and prev != "\\" and not in_single:
            in_double = not in_double
            current.append(ch)
            continue
        if not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
                continue
        current.append(ch)
    if current:
        args.append("".join(current).strip())
    return args


def _parse_column_ref(raw: str) -> int | str:
    value = strip_quotes(raw)
    return int(value) if value.isdigit() else value

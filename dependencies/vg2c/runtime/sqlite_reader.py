"""SqliteReader - run SQL joins over CSV files using in-memory SQLite."""

from __future__ import annotations

import csv
from contextlib import closing
from functools import partial
import re
import sqlite3

import pandas as pd

from vg2c.runtime.file_store import input_path
from vg2c.runtime.values import resolve_path
from vg2c.runtime.csv_io import _CsvIO
from vg2c.runtime.crosstab import _CrosstabUtility


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _ContinuousPercentile:
    def __init__(self, fraction: float) -> None:
        self._fraction = fraction
        self._values: list[float] = []

    def step(self, value) -> None:
        if value is not None:
            self._values.append(float(value))

    def finalize(self):
        if not self._values:
            return None
        values = sorted(self._values)
        position = (len(values) - 1) * self._fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)


class _SqliteReader:
    """Run SQL joins over CSV files using in-memory SQLite."""


    @staticmethod
    def _load_csv_as_table(
        conn: sqlite3.Connection, csv_path: str, table_name: str | None = None, *, workdir=None, files=None
    ) -> str:
        path = input_path(csv_path, workdir, files) if workdir is not None else resolve_path(csv_path)
        table_name = table_name or path.stem
        table_ident = _quote_identifier(table_name)

        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh, delimiter=_CsvIO._delimiter_for(str(path)))
            rows = list(reader)

        if reader.fieldnames is None:
            conn.execute(f"DROP TABLE IF EXISTS {table_ident}")
            conn.execute(f'CREATE TABLE {table_ident} ("_empty" TEXT)')
            return table_name

        cols = list(reader.fieldnames)
        if not cols:
            conn.execute(f"DROP TABLE IF EXISTS {table_ident}")
            conn.execute(f'CREATE TABLE {table_ident} ("_empty" TEXT)')
            return table_name

        folded = [name.casefold() for name in cols]
        if any(not name for name in cols) or len(set(folded)) != len(folded):
            raise ValueError(f"CSV input has empty or duplicate column names: {path}")
        col_defs = ", ".join(f"{_quote_identifier(c)} TEXT" for c in cols)
        conn.execute(f"DROP TABLE IF EXISTS {table_ident}")
        conn.execute(f"CREATE TABLE {table_ident} ({col_defs})")

        header_str = [str(c) for c in cols]
        filtered_rows = [
            row for row in rows if [str(row.get(c, "")) for c in cols] != header_str
        ]

        if filtered_rows:
            placeholders = ", ".join("?" for _ in cols)
            conn.executemany(
                f"INSERT INTO {table_ident} VALUES ({placeholders})",
                [[row.get(c, "") for c in cols] for row in filtered_rows],
            )

        return table_name

    @classmethod
    def _split_statements(cls, sql: str) -> list[str]:
        statements = []
        start = 0
        for index, char in enumerate(sql):
            if char == ";" and sqlite3.complete_statement(sql[start:index + 1]):
                statements.append(sql[start:index + 1])
                start = index + 1
        tail = sql[start:]
        if re.sub(r"--[^\n]*|/\*.*?\*/", "", tail, flags=re.DOTALL).strip():
            statements.append(tail)
        return statements

    def execute(self, sql: str, inputs: list[str | tuple[str, str]], *, params=None, workdir=None, files=None) -> pd.DataFrame:
        stmts = self._split_statements(sql)
        if params and len(stmts) != 1:
            raise ValueError("SQL binds require exactly one statement")
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.create_aggregate("P25", 1, partial(_ContinuousPercentile, 0.25))
            conn.create_aggregate("P75", 1, partial(_ContinuousPercentile, 0.75))

            for input_spec in inputs:
                if isinstance(input_spec, tuple):
                    csv_path, table_name = input_spec
                else:
                    csv_path, table_name = input_spec, None
                self._load_csv_as_table(conn, csv_path, table_name, workdir=workdir, files=files)

            if not stmts:
                return pd.DataFrame()

            for stmt in stmts[:-1]:
                try:
                    conn.execute(stmt)
                except sqlite3.Error as exc:
                    raise RuntimeError(
                        f"SQLite error in setup statement: {exc}\nSQL:\n{stmt}"
                    ) from exc

            final_stmt = stmts[-1]

            alias_to_table: dict[str, str] = {}
            alias_map_re = re.compile(
                r"\b(?:FROM|JOIN)\s+(?:\[([^\]]+)\]|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))\s+([A-Za-z_][A-Za-z0-9_]*)\b",
                re.IGNORECASE,
            )
            for match in alias_map_re.finditer(final_stmt):
                table_name = match.group(1) or match.group(2) or match.group(3)
                alias = match.group(4)
                if table_name and alias:
                    alias_to_table[alias.lower()] = table_name

            def _lookup_alias_columns(alias: str) -> list[str]:
                table_name = alias_to_table.get(alias.lower())
                if not table_name:
                    return []
                pragma_rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                return [str(row[1]) for row in pragma_rows if len(row) > 1]

            final_stmt = _CrosstabUtility.substitute_sql(
                final_stmt,
                alias_columns_lookup=_lookup_alias_columns,
            )

            try:
                cursor = conn.execute(final_stmt, params or {})
                rows = cursor.fetchall()
                col_names = [d[0] for d in cursor.description] if cursor.description else []
            except sqlite3.Error as exc:
                raise RuntimeError(
                    f"SQLite error in execute: {exc}\nSQL:\n{final_stmt}"
                ) from exc

        if not rows:
            return pd.DataFrame(columns=col_names)

        # Retain positional values even if SQL returns repeated column labels.
        # Constructing dicts here would silently discard duplicate-name values.
        return pd.DataFrame([tuple(row) for row in rows], columns=col_names)

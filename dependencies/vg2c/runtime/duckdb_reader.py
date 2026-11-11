"""DuckDbReader - run local SQL queries over CSV, TSV, and Parquet inputs."""

from __future__ import annotations

import re

import duckdb
import pandas as pd

from vg2c.runtime.values import resolve_path
from vg2c.runtime.file_store import MemoryFile, input_path


class _DuckDbReader:
    """Run DuckDB SQL using explicit local file bindings."""

    _MARKER = re.compile(r"<DDBTable>([^<]+)</DDBTable>", re.IGNORECASE)

    @staticmethod
    def _relation(path) -> str:
        literal = str(path).replace("'", "''")
        suffix = path.suffix.lower()
        if suffix == ".tab":
            return f"read_csv_auto('{literal}', delim='\\t', sample_size=-1)"
        if suffix == ".csv":
            return f"read_csv_auto('{literal}', sample_size=-1)"
        if suffix == ".parquet":
            return f"read_parquet('{literal}')"
        raise ValueError(f"DuckDB input has unsupported extension: {path}")

    def execute(
        self, sql: str, inputs: list[str | tuple[str, str]], *, params=None, workdir=None, files=None
    ) -> pd.DataFrame:
        relations = {}
        memory_inputs = {}
        for input_spec in inputs:
            if not isinstance(input_spec, tuple) or len(input_spec) != 2:
                raise ValueError("DuckDB inputs require explicit <file>:<table> bindings")
            source, table_name = input_spec
            key = table_name.casefold() if isinstance(table_name, str) else ""
            if not key or key in relations:
                raise ValueError(f"DuckDB input has invalid or duplicate table name: {table_name!r}")
            path = input_path(source, workdir, files) if workdir is not None else resolve_path(source)
            if isinstance(path, MemoryFile):
                if path.suffix.lower() not in {".csv", ".tab"}:
                    raise ValueError(f"DuckDB input has unsupported extension: {path}")
                name = f"vg2c_memory_{len(memory_inputs)}"
                memory_inputs[name] = path
                relations[key] = f'"{name}"'
            else:
                relations[key] = self._relation(path)

        if "<DDBTABLE" in sql.upper() or "</DDBTABLE" in sql.upper():
            def replace(match):
                table_name = match.group(1).strip().casefold()
                if table_name not in relations:
                    raise ValueError(f"DuckDB query references unknown DDBTable marker: {table_name}")
                return relations[table_name]
            sql = self._MARKER.sub(replace, sql)
            if "<DDBTABLE" in sql.upper() or "</DDBTABLE" in sql.upper():
                raise ValueError("DuckDB query has malformed DDBTable marker")

        try:
            with duckdb.connect(":memory:") as conn:
                for name, path in memory_inputs.items():
                    with path.open() as stream:
                        conn.read_csv(stream, delimiter="\t" if path.suffix.lower() == ".tab" else ",",
                                      sample_size=-1).create(name)
                return conn.execute(sql, params or {}).df()
        except duckdb.Error as exc:
            raise RuntimeError(f"DuckDB error in execute: {exc}\nSQL:\n{sql}") from exc

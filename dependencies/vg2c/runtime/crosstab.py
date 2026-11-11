"""Crosstab pivot utility for DataFrames."""

from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd


class _CrosstabUtility:
    # SPFUtilities/utils.py:4120: ScriptHost-entry default. Standalone mode
    # defaults to 1,000,000 and an explicit pivotReadChunkSize can override it;
    # vg2c's normal SQL entry has no corresponding runtime-mode signal yet.
    _CHUNK_ROWS = 50_000
    TOKEN = "CrossTab->[["
    TOKEN_RE = re.compile(
        r"(?P<prefix>,?)\s*CrossTab->\[\[\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
        r"(?P<instance>[^;\]]+)\s*;\s*:(?P<mode>[YyNn])\s*\]\](?P<suffix>,?)"
    )

    @classmethod
    def has_token(cls, value: str | None) -> bool:
        return bool(value and cls.TOKEN in value)

    @staticmethod
    def _extract_selected_columns_by_alias(sql: str) -> dict[str, set[str]]:
        by_alias: dict[str, set[str]] = {}
        match = re.search(
            r"\bSELECT\b(?P<select_part>.*?)\bFROM\b",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return by_alias

        select_part = match.group("select_part")
        col_ref_re = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*"
            r'(?:\[([^\]]+)\]|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))'
        )
        for col_match in col_ref_re.finditer(select_part):
            alias = col_match.group(1).lower()
            col_name = col_match.group(2) or col_match.group(3) or col_match.group(4)
            if not col_name:
                continue
            by_alias.setdefault(alias, set()).add(col_name.lower())

        return by_alias

    @classmethod
    def substitute_sql(
        cls,
        sql: str,
        alias_columns_lookup: Callable[[str], list[str]] | None = None,
    ) -> str:
        if alias_columns_lookup is None or not cls.has_token(sql):
            return sql

        selected_by_alias = cls._extract_selected_columns_by_alias(sql)

        def _replace(match: re.Match[str]) -> str:
            prefix = match.group("prefix")
            suffix = match.group("suffix")
            alias = match.group("alias").strip()
            mode = match.group("mode").upper()
            all_cols = alias_columns_lookup(alias)
            selected = selected_by_alias.get(alias.lower(), set())
            dynamic_cols = [c for c in all_cols if c.lower() not in selected]

            if not dynamic_cols:
                return ""

            if mode == "N":
                body = ",".join(dynamic_cols)
                return f"{prefix}{body}{suffix}"

            body = "\n         ,".join(f"{alias}.[{c}] AS [{c}]" for c in dynamic_cols)
            return f"{prefix}{body}{suffix}"

        return cls.TOKEN_RE.sub(_replace, sql)

    @staticmethod
    def _names(rows: pd.DataFrame) -> dict[str, str]:
        """Resolve the original result schema without silently losing duplicate fields."""
        seen: dict[str, str] = {}
        for column in rows.columns:
            label = str(column)
            folded = label.casefold()
            if folded in seen:
                raise ValueError(f"Ambiguous crosstab source column {label!r}")
            seen[folded] = label
        return seen

    @staticmethod
    def _header_name(name: str, legacy_headers: bool) -> str:
        name = name.upper() if name else "_UNKNOWN_"
        if legacy_headers:
            name = re.sub(r"[^\w#@$_-]", "#", re.sub(r"\s", "_", name))
        return name

    @staticmethod
    def _sort_result(result: pd.DataFrame, specification: str) -> pd.DataFrame:
        cols: list[str] = []
        ascend: list[bool] = []
        numeric: dict[str, pd.Series] = {}
        for term in specification.split(","):
            match = re.fullmatch(r"\s*(\[.*?\]|[^\s]+)(?:\s+(ASC|DESC)(-1)?)?\s*",
                                 term, re.IGNORECASE)
            if match is None:
                raise ValueError(f"Unsupported pivot sort term: {term!r}")
            col = match.group(1).strip("[]").casefold()
            lookup = {str(x).casefold(): x for x in result.columns}
            if col not in lookup:
                raise ValueError(f"Pivot sort column not found: {col}")
            actual = lookup[col]
            cols.append(actual)
            ascend.append((match.group(2) or "ASC").upper() == "ASC")
            if match.group(3):
                numeric[actual] = pd.to_numeric(result[actual], errors="raise")
        if numeric:
            # Sorting on numerical shadow columns preserves original literal IDs.
            result = result.assign(**{f"__pivot_sort_{i}": val for i, val
                                      in enumerate(numeric.values())})
            replacements = {key: f"__pivot_sort_{i}" for i, key in enumerate(numeric)}
            cols = [replacements.get(col, col) for col in cols]
            result = result.sort_values(cols, ascending=ascend, kind="stable")
            return result.drop(columns=list(replacements.values())).reset_index(drop=True)
        return result.sort_values(cols, ascending=ascend, kind="stable").reset_index(drop=True)

    def apply(
        self,
        rows: pd.DataFrame,
        row_keys: list[str] | None = None,
        header_key: str | None = None,
        value_key: str | None = None,
        *,
        pivot_columns: str | None = None,
        pivot_values: str | list[str] | None = None,
        duplicate: str = "first",
        missing: str = "",
        dot: bool = False,
        sort: str | None = None,
        legacy_headers: bool = False,
    ) -> pd.DataFrame:
        """ScriptHost-style pivot with a validated old-JSON input adapter.

        Source: SPFUtilities/utils.py PivotTable()/pivotDF() (3991-4512).
        Both APIs infer grouping keys from the materialized query result.
        """
        if row_keys is not None:
            if pivot_columns is not None or pivot_values is not None:
                raise ValueError("Cannot mix legacy crosstab and new pivot configuration")
            # Old JSON fields are an input-format adapter, not an alternative
            # vg2c-specific aggregation or row-selection implementation.
            pivot_columns, pivot_values = header_key, value_key
        if not isinstance(pivot_columns, str) or not pivot_columns.strip():
            raise ValueError("Pivot requires pivot_columns")
        if isinstance(pivot_values, str):
            value_names = [part.strip() for part in pivot_values.split(",")]
        elif isinstance(pivot_values, list):
            value_names = pivot_values
        else:
            raise ValueError("Pivot requires pivot_values as a string or string list")
        if not value_names or any(not isinstance(v, str) or not v.strip()
                                  for v in value_names):
            raise ValueError("Pivot value column names must be nonempty strings")
        if "," in pivot_columns:
            # Original getUniqueValuesInColumn() reads usecols=[CTHeader] even
            # though an earlier expression splits CTHeader. Not proven supported.
            raise ValueError("Multi-column CTHEADER is not verified in normal ScriptHost")
        if row_keys is not None and (not isinstance(row_keys, list)
                                     or not all(isinstance(v, str) and v for v in row_keys)):
            raise ValueError("Legacy row_keys must be a list of nonempty strings")
        lookup = self._names(rows)
        header_field = pivot_columns.strip()
        names = [header_field, *value_names]
        if len(set(x.casefold() for x in names)) != len(names):
            raise ValueError("Pivot header and value columns must be distinct")
        required = [*names, *(row_keys or [])]
        missing_names = [name for name in required if name.casefold() not in lookup]
        if missing_names:
            raise ValueError(f"Crosstab is missing columns: {missing_names}")

        excluded = {name.casefold() for name in names}
        inferred = [str(name) for name in rows.columns
                    if str(name).casefold() not in excluded]
        if row_keys is not None and [key.casefold() for key in row_keys] != [
            key.casefold() for key in inferred
        ]:
            raise ValueError(
                "Legacy crosstab row_keys do not match ScriptHost-inferred grouping "
                f"columns {inferred!r}. Update the JSON row_keys to match the SQL "
                "result, or use pivot_columns/pivot_values instead."
            )
        if not inferred:
            raise ValueError("Pivot requires at least one row identifier column")
        if len(set(name.casefold() for name in [*inferred, *names])) != len(inferred) + len(names):
            raise ValueError("Crosstab row, header and value keys must be distinct")

        # The original normal path reads a CSV intermediate with dtype=object
        # and na_filter=False. Do not coerce source identifiers to numbers.
        frame = rows.copy()
        frame.columns = [str(name).upper() for name in frame.columns]
        frame = frame.astype(object).where(pd.notna(frame), "")
        frame = frame.map(str)
        group_cols = [name.upper() for name in inferred]
        pivot_field = header_field.upper()
        value_fields = [name.upper() for name in value_names]
        output_groups = [name.lower() if dot else name for name in group_cols]

        if frame.empty:
            return pd.DataFrame(columns=output_groups)

        # Original ScriptHost uppercases pivot identities only when its final
        # header collision flag is set (utils.py:4304-4346). That flag can
        # remain false for 'a'/'A', yielding ambiguous output names. Normalize
        # the identities early as an explicitly documented source bug fix.
        frame[pivot_field] = frame[pivot_field].str.upper()

        # Source utils.py:4330-4361 uses positional FIRST/LAST per read chunk,
        # then combines overlapping rows via combine_first() (4408-4427).
        # Consequently, LAST across a chunk boundary still favors the earlier
        # chunk's non-missing value.
        keep = duplicate.strip().lower() if isinstance(duplicate, str) else "first"
        if keep not in ("first", "last"):
            keep = "first"
        source_values = list(frame[pivot_field].unique())
        separator = "." if dot else "@"
        rename: dict[tuple[str, object], str] = {}
        for value_col in value_fields:
            for pivot_value in source_values:
                stem = self._header_name(str(pivot_value), legacy_headers)
                label = f"{value_col}{separator}{stem}" if len(value_fields) > 1 else stem
                rename[(value_col, pivot_value)] = label
        pivot_cols = sorted(set(rename.values()))
        group_output_names = [name.upper() for name in group_cols]
        if len(set(x.casefold() for x in [*group_output_names, *pivot_cols])) != (
            len(group_output_names) + len(pivot_cols)
        ):
            raise ValueError("Crosstab pivot headers collide with row or other headers")
        if len(pivot_cols) < len(rename):
            raise ValueError("Crosstab pivot headers collide after normalization")

        wide = None
        for offset in range(0, len(frame), self._CHUNK_ROWS):
            chunk = frame.iloc[offset:offset + self._CHUNK_ROWS]
            chunk = chunk.drop_duplicates(subset=[*group_cols, pivot_field], keep=keep)
            pivoted = chunk.set_index([*group_cols, pivot_field])[value_fields].unstack(pivot_field)
            pivoted.columns = [rename[(str(value).upper(), pivot_value)]
                               for value, pivot_value in pivoted.columns]
            wide = pivoted if wide is None else wide.combine_first(pivoted)
        wide = wide.reset_index()
        wide = wide.reindex(columns=[*group_output_names, *pivot_cols])
        wide = wide.where(pd.notna(wide), missing)
        if dot:
            wide.columns = [str(name).lower() for name in wide.columns]
        if sort:
            wide = self._sort_result(wide, sort)
        wide.attrs["pivot_headers"] = [name.lower() if dot else name for name in pivot_cols]
        return wide

    @staticmethod
    def write_header_list(workdir, reference: str, headers: list[str], files=None) -> None:
        """Save CTARRAY's narrowly-scoped header dataflow artifact.

        SPFUtilities/utils.py Save_CT_Header() (4514-4549) uses
        <instance>_<alias>.ini. Tabs bridge a source CSV/INI delimiter bug.
        """
        from pathlib import Path

        parts = [p.strip() for p in reference.split(",")]
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z_]\w*", parts[0]) or (
            not re.fullmatch(r"\d+", parts[1])
        ):
            raise ValueError("CTARRAY expects alias,instance (for example a0,4253)")
        path = Path(workdir).resolve() / f"{parts[1]}_{parts[0].upper()}.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\t".join(headers), encoding="utf-8")
        if files is not None:
            files.pop(path.resolve(), None)

    @classmethod
    def substitute_header_lists(cls, sql: str, workdir, files=None) -> str:
        """Expand source-style numbered CrossTab references before SQL execution.

        SPFSQL3.py SubStitute_CT() (4509-4777); leaves nonnumeric legacy
        alias-schema tokens to substitute_sql() for older generated projects.
        """
        token = re.compile(
            r"(?P<prefix>,\s*)?CrossTab->\[\[\s*(?P<alias>[A-Za-z_]\w*)\s*,\s*"
            r"(?P<instance>\d+)\s*(?:;\s*(?P<function>.*?))?"
            r":\s*(?P<mode>[YNAyna])\s*\]\]"
            # Consume a following separator only when there is no leading one.
            r"(?(prefix)|(?P<suffix>\s*,)?)",
            re.IGNORECASE | re.DOTALL,
        )

        def replace(match: re.Match[str]) -> str:
            alias = match.group("alias")
            instance = match.group("instance")
            mode = match.group("mode").upper()
            from vg2c.runtime.file_store import input_path

            path = input_path(f"{instance}_{alias.upper()}.ini", workdir, files)
            if not path.exists():
                raise FileNotFoundError(f"CrossTab header metadata missing: {path}")
            columns = [name.strip() for name in path.read_text(encoding="utf-8").split("\t") if name.strip()]
            function = (match.group("function") or "").strip().rstrip(";")
            if function and "|<>|" not in function:
                raise ValueError(f"CrossTab expression must include |<>|: {match.group(0)}")
            # A zero-row pivot writes an empty header list; omit its projection.
            if not columns:
                return ""
            expressions = []
            for column in columns:
                if "]" in column:
                    raise ValueError(f"CrossTab header cannot be quoted safely: {column!r}")
                ref = f"[{column}]" if mode != "Y" else f"{alias}.[{column}]"
                expr = function.replace("|<>|", ref) if function else ref
                if mode != "A":
                    expr += f" AS [{column}]"
                expressions.append(expr)
            return (match.group("prefix") or "") + ", ".join(expressions) + (
                match.group("suffix") or ""
            )

        return token.sub(replace, sql)

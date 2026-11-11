"""Read an editable SQL asset and execute it with explicit ownership."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import inspect
import json
import os
import re
import sys

from vg2c.runtime.csv_io import _CsvIO as CsvIO
from vg2c.runtime.crosstab import _CrosstabUtility as CrosstabUtility
from vg2c.runtime.duckdb_reader import _DuckDbReader as DuckDbReader
from vg2c.runtime.environment import DATABASE_ENVIRONMENT_LOCK
from vg2c.runtime.sqlite_reader import _SqliteReader as SqliteReader
from vg2c.runtime.sql_text import scan_sql_get_csv_list_calls
from vg2c.runtime.values import job_path, substitute


def node_site(node: str) -> str:
    """Use the existing dispatch rule: the first literal node component is the site."""
    match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)(?:\.|$)", node.strip())
    if match is None:
        raise ValueError(f"Cannot resolve backend site from node {node!r}")
    return match.group(1)


def _required_params(function) -> list[str]:
    return [
        p.name
        for p in inspect.signature(function).parameters.values()
        if p.name != "self"
        and p.default is p.empty
        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]


def _reader_credentials(reader_class, environ):
    parameters = inspect.signature(reader_class.__init__).parameters
    if not {"username", "password"}.issubset(parameters):
        return {}
    environ = os.environ if environ is None else environ
    username, password = environ.get("NAME"), environ.get("PW")
    if not username and not password:
        return {}
    if (
        not isinstance(username, str)
        or not username.strip()
        or not isinstance(password, str)
        or not password
    ):
        raise ValueError("Set both NAME and PW through job.environ or runtime Secrets")
    return {"username": username, "password": password}


@contextmanager
def _reader_environment(reader, environ):
    credentials = _reader_credentials(type(reader), environ)
    if not credentials or not sys.platform.startswith("linux"):
        yield
        return
    key = credentials["username"].rsplit("\\", 1)[-1].upper()
    with DATABASE_ENVIRONMENT_LOCK:
        previous = os.environ.get(key)
        os.environ[key] = credentials["password"]
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def resolve_reader(node=None, *, environ=None):
    """No node -> local SQLite; else the datasyncx reader whose name appears in the node."""
    if not node:
        return SqliteReader()
    import datasyncx

    lowered = node.lower()
    tokens = {}
    for name in dir(datasyncx):
        token = name[: -len("Reader")].lower()
        if name.endswith("Reader") and token and token in lowered:
            tokens.setdefault(token, name)
    # Prefer the longest token, e.g. "mongodb" over "mongo".
    matches = [
        name
        for token, name in tokens.items()
        if not any(token != other and token in other for other in tokens)
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Cannot infer reader from node {node!r}: matches {sorted(matches)}"
        )
    if not matches:
        site, dot, database = node.strip().rpartition(".")
        if not (dot and site and database):
            raise ValueError(
                f"Cannot infer reader from node {node!r}: no datasyncx reader name "
                "in node and it is not a SITE.DATABASE Oracle node"
            )
        reader_class = datasyncx.OracleReader
        reader_kwargs = {"node": node.strip()}
    else:
        name = matches[0]
        reader_class = getattr(datasyncx, name)
        parameters = inspect.signature(reader_class.__init__).parameters
        node_kwarg = next((key for key in ("node", "nodes") if key in parameters), None)
        reader_kwargs = {node_kwarg: node} if node_kwarg else {}

    credentials = _reader_credentials(reader_class, environ)
    if matches:
        missing = [
            p for p in _required_params(reader_class.__init__)
            if p not in reader_kwargs and p not in credentials
        ]
        missing += [
            p for p in _required_params(reader_class.read) if p not in {"site", "query"}
        ]
        if missing:
            raise ValueError(
                f"{name} for node {node!r} needs {missing}; pass reader= explicitly"
            )
    return reader_class(**reader_kwargs, **credentials)


_OUTPUT_HEADERS = "-- vg2c-output-headers: "


def _sql_output_headers(source: str, header):
    """Read an optional legacy output-name override from the editable SQL asset.

    Ordinary queries use the result column names (including SQL AS aliases).
    The directive exists only for explicit VG2 /HEADERS semantics, which can
    select columns by name rather than rename them positionally.
    """
    if not source.startswith(_OUTPUT_HEADERS):
        return source, header
    first, newline, sql = source.partition("\n")
    if not newline:
        raise ValueError("Malformed vg2c-output-headers directive: missing query body")
    try:
        names = json.loads(first[len(_OUTPUT_HEADERS) :])
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid vg2c-output-headers JSON list") from exc
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("vg2c-output-headers must be a list of strings")
    if header is not None:
        raise ValueError(
            "vg2c-output-headers cannot be combined with a separate header"
        )
    return sql, names


def execute_sql(
    path,
    *,
    output,
    workdir,
    reader=None,
    environ=None,
    values=None,
    macros=None,
    inputs=None,
    header=None,
    crosstab=None,
    node=None,
    params=None,
    files=None,
    pivot_columns=None,
    pivot_values=None,
    pivot_duplicate="first",
    pivot_missing="",
    pivot_dot=False,
    pivot_sort=None,
    pivot_header_ref=None,
    pivot_legacy_headers=False,
):
    """Reread the asset on every call; routing is separate from SQL binds."""
    path = job_path(path, workdir)
    with path.open(encoding="utf-8", newline="") as stream:
        sql = stream.read()
    try:
        sql, header = _sql_output_headers(sql, header)
        return run_query(
            sql,
            reader=reader,
            environ=environ,
            output=output,
            workdir=workdir,
            values=values,
            macros=macros,
            inputs=inputs,
            header=header,
            files=files,
            crosstab=crosstab,
            node=node,
            params=params,
            pivot_columns=pivot_columns,
            pivot_values=pivot_values,
            pivot_duplicate=pivot_duplicate,
            pivot_missing=pivot_missing,
            pivot_dot=pivot_dot,
            pivot_sort=pivot_sort,
            pivot_header_ref=pivot_header_ref,
            pivot_legacy_headers=pivot_legacy_headers,
        )
    except (ValueError, RuntimeError) as exc:
        raise type(exc)(f"{path}: {exc}") from exc


def run_query(
    sql,
    *,
    output,
    workdir,
    reader=None,
    environ=None,
    values=None,
    macros=None,
    inputs=None,
    header=None,
    crosstab=None,
    node=None,
    params=None,
    files=None,
    pivot_columns=None,
    pivot_values=None,
    pivot_duplicate="first",
    pivot_missing="",
    pivot_dot=False,
    pivot_sort=None,
    pivot_header_ref=None,
    pivot_legacy_headers=False,
):
    """Single query body extracted from PipelineContext.run_query."""
    configured_pivot = pivot_columns is not None or pivot_values is not None
    if crosstab is not None and (configured_pivot or pivot_header_ref is not None):
        raise ValueError("Cannot specify both legacy crosstab and new pivot arguments")
    if (pivot_columns is None) != (pivot_values is None):
        raise ValueError("pivot_columns and pivot_values must be supplied together")
    if not configured_pivot and (
        pivot_header_ref is not None
        or pivot_sort is not None
        or pivot_missing != ""
        or pivot_dot
        or pivot_legacy_headers
        or pivot_duplicate != "first"
    ):
        raise ValueError("Pivot modifiers require pivot_columns and pivot_values")
    if crosstab is not None:
        if not isinstance(crosstab, dict):
            raise ValueError("Legacy crosstab must be a mapping")
        keys = ("row_keys", "header_key", "value_key")
        if any(key not in crosstab for key in keys):
            raise ValueError("Legacy crosstab requires row_keys, header_key, value_key")
        unknown = sorted(set(crosstab) - set(keys))
        if unknown:
            raise ValueError(
                f"Unsupported legacy crosstab configuration fields: {unknown}. "
                "Use pivot_columns/pivot_values for ScriptHost-style options."
            )
        if (
            not isinstance(crosstab["row_keys"], list)
            or not all(isinstance(k, str) and k for k in crosstab["row_keys"])
            or not all(
                isinstance(crosstab[k], str) and crosstab[k]
                for k in ("header_key", "value_key")
            )
        ):
            raise ValueError(
                "Legacy crosstab keys must be strings; row_keys a string list"
            )
    if header is not None and (
        not isinstance(header, list)
        or not all(isinstance(item, str) for item in header)
    ):
        raise ValueError("SQL header must be a list of strings")
    inferred_reader = reader is None
    if inferred_reader:
        reader = resolve_reader(
            substitute(node, values=values, macros=macros) if node else None,
            environ=environ,
        )
    sql = substitute(sql, values=values, macros=macros)
    sql = CrosstabUtility.substitute_header_lists(sql, workdir, files=files)
    calls = scan_sql_get_csv_list_calls(sql)
    for call in reversed(calls):
        replacement = CsvIO(workdir=workdir, files=files).sql_get_csv_list(
            call.source_path, call.column_ref, call.lead_in
        )
        if call.needs_closing_paren:
            replacement += ")"
        sql = sql[: call.start] + replacement + sql[call.end :]
    method = getattr(reader, "execute", None) or reader.read
    if params:
        if "params" not in inspect.signature(method).parameters:
            raise ValueError(f"{type(reader).__name__} does not support SQL binds")
        if len(SqliteReader._split_statements(sql)) != 1:
            raise ValueError("SQL binds require exactly one statement")
    kwargs = {"params": params} if params else {}
    if isinstance(reader, (SqliteReader, DuckDbReader)):
        kwargs["workdir"] = workdir
        kwargs["files"] = files
    if hasattr(reader, "execute"):
        resolved_inputs = [
            (
                (
                    str(
                        job_path(
                            substitute(item[0], values=values, macros=macros), workdir
                        )
                    ),
                    item[1],
                )
                if isinstance(item, tuple)
                else str(
                    job_path(substitute(item, values=values, macros=macros), workdir)
                )
            )
            for item in (inputs or [])
        ]
        result = reader.execute(sql, resolved_inputs, **kwargs)
    else:
        if not node:
            raise ValueError("An external reader requires an explicit node/site")
        try:
            with (
                _reader_environment(reader, environ)
                if inferred_reader else nullcontext()
            ):
                result = reader.read(
                    site=node_site(substitute(node, values=values, macros=macros)),
                    query=sql,
                    **kwargs,
                )
        finally:
            if type(reader).__module__.startswith("datasyncx"):
                from vg2c.runtime.oracle_client import _OracleClient as OracleClient

                OracleClient.log_active_client()
        result.columns = [col.lower() for col in result.columns]
    if crosstab is not None:
        resolved_crosstab = {
            key: (
                [
                    (
                        substitute(item, values=values, macros=macros)
                        if isinstance(item, str)
                        else item
                    )
                    for item in value
                ]
                if isinstance(value, list)
                else (
                    substitute(value, values=values, macros=macros)
                    if isinstance(value, str)
                    else value
                )
            )
            for key, value in crosstab.items()
        }
        result = CrosstabUtility().apply(result, **resolved_crosstab)
    elif configured_pivot:
        pivot_columns = substitute(pivot_columns, values=values, macros=macros)
        if isinstance(pivot_values, list):
            pivot_values = [
                substitute(item, values=values, macros=macros) for item in pivot_values
            ]
        else:
            pivot_values = substitute(pivot_values, values=values, macros=macros)
        result = CrosstabUtility().apply(
            result,
            pivot_columns=pivot_columns,
            pivot_values=pivot_values,
            duplicate=pivot_duplicate,
            missing=pivot_missing,
            dot=pivot_dot,
            sort=pivot_sort,
            legacy_headers=pivot_legacy_headers,
        )
        if pivot_header_ref is not None:
            ref = substitute(pivot_header_ref, values=values, macros=macros)
            CrosstabUtility.write_header_list(
                workdir, ref, result.attrs.get("pivot_headers", []), files=files
            )
    if header:
        header = [substitute(item, values=values, macros=macros) for item in header]
    CsvIO(workdir=workdir, files=files).write(
        str(job_path(substitute(str(output), values=values, macros=macros), workdir)),
        result,
        header=header,
    )
    return result

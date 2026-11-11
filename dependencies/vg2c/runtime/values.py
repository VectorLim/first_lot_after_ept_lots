"""Execution-time values and explicit filesystem roots."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import os
import re
from pathlib import Path
from collections.abc import Mapping

NAMED_MACRO_RE = re.compile(r"<<<(.+?)>>>", re.DOTALL)


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def normalize_macro_name(raw: str) -> str:
    name = raw.strip()
    if name.startswith("<<<") and name.endswith(">>>"):
        name = name[3:-3]
    return name.strip().upper()


def resolve_path(name: str | Path, *, for_write: bool = False, workdir: str | Path | None = None) -> Path:
    """Resolve runtime paths without loading compiler utilities."""
    path = Path(name)
    if workdir is not None:
        return path if path.is_absolute() else Path(workdir).resolve() / path
    base_dir = Path.cwd()

    if path.is_absolute():
        if for_write or path.exists():
            return path
        local_fallback = Path(path.name)
        if local_fallback.exists():
            return local_fallback
        return path

    base_path = base_dir / path
    if for_write:
        return base_path

    if path.exists():
        return path
    return base_path


def snapshot_values(workdir, *, values=None, environ=None) -> dict[str, str]:
    # Adapted from SPFLib/SPFGlobals.py:453-502 and utils.py:1606-1643; fresh per call.
    now = datetime.now()
    result = {
        "SPF-JOB-START-DAY": now.strftime("%Y-%m-%d 00:00:00"),
        "SPF-JOB-START-TIME": now.strftime("%Y-%m-%d %H:%M:%S"),
        "SPF-JOB-START-GMT-TIME": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "SPF-DEFAULT-DIR": str(Path(workdir).resolve()) + os.sep,
    }
    result.update({str(key).upper(): value for key, value in (values or {}).items()})
    result.update({f"%{key}%".upper(): value for key, value in
                   (os.environ if environ is None else environ).items()})
    for key in ("SPF-DEFAULT-DIR", "SPF-DEFAULT-EXE", "SPF-DEFAULT-EXE2"):
        if key in result and not str(result[key]).endswith(os.sep):
            result[key] = str(result[key]) + os.sep
    return result


def job_path(path: str | Path, workdir: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else Path(workdir).resolve() / path


def substitute(text: str, *, values: Mapping | None = None, macros: Mapping | None = None) -> str:
    """Resolve named values once; keep blank values and original newlines."""
    globals_map = {str(key).upper(): value for key, value in (values or {}).items()}
    macro_map = {str(key).upper(): value for key, value in (macros or {}).items()}

    def replace(match: re.Match) -> str:
        key = match.group(1).strip().upper()
        mapping = globals_map if key.startswith(("CL_", "SPF-JOB-", "SPF-DEFAULT-", "%")) else macro_map
        if key not in mapping:
            if key.startswith(("SPF-", "SPF$", "!")) or key == "SPF_DATETIME":
                return match.group(0)
            raise ValueError(f"Unknown value {match.group(0)!r}")
        value = mapping[key]
        if mapping is globals_map and key.startswith("%") and not value:
            raise ValueError(f"Missing value {match.group(0)!r}")
        return "" if value is None else str(value)

    result = NAMED_MACRO_RE.sub(replace, text)
    # Adapted from SPFUtilities/utils.py:1706-1739,2052-2066; preserve reserved macros.
    if "<<<%" in result or "%>>>" in result:
        raise ValueError("Malformed environment token; expected <<<%NAME%>>>")
    for match in re.finditer(r"<<<([\s\w%-]+)(?!>)", result):
        tail = result[match.start():]
        if ">>>" not in tail and not match.group(1).strip().upper().startswith(("SPF-", "%")):
            raise ValueError(f"Missing closing macro token: {match.group(0)!r}")
    if "<<>>" in result:
        raise ValueError("Positional macro <<>> has no supported source cursor")
    return result


def read_macro_row(path: str | Path, *, workdir: str | Path, files=None) -> dict[str, str] | None:
    # Adapted from scripthost-utilities-decompiled/SPSQL3_py/SPFLib/SPFSQL3.py:12491-12515;
    # test_direct_runtime.py covers first-row/zero-row behavior; no child execution here.
    from vg2c.runtime.file_store import input_path

    with input_path(path, workdir, files).open(newline="", encoding="utf-8", errors="replace") as stream:
        row = next(csv.DictReader(stream), None)
        if row is None:
            return None
        # Adapted from SPFUtilities/utils.py:GetHeadersFromFile (2723-2746).
        return {key.strip().replace("[", "(").replace("]", ")"): value
                for key, value in row.items() if key is not None}

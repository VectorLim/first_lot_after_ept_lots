"""CsvIO - lightweight CSV reader/writer over stdlib csv."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

import pandas

from vg2c.runtime.values import resolve_path
from vg2c.runtime.file_store import input_path


class _CsvIO:
    """Read and write CSV files relative to the runtime script directory."""


    def __init__(self, *, workdir: str | Path | None = None, files=None) -> None:
        self.workdir = workdir
        self.files = files

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def iter(self, name: str) -> Iterator[dict[str, str]]:
        """Yield each data row as a dict keyed by header names."""
        path = input_path(name, self.workdir, self.files) if self.workdir is not None else resolve_path(name)
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh, delimiter=self._delimiter_for(str(path)))
            yield from reader

    def single_row(self, name: str) -> dict[str, str]:
        """Return exactly one data row from *name*; raise on 0 or >1 rows."""
        rows = self.iter(name)
        first = next(rows, None)
        if first is None:
            raise ValueError(f"CSV '{name}' must contain exactly 1 data row; found 0")
        second = next(rows, None)
        if second is not None:
            raise ValueError(f"CSV '{name}' must contain exactly 1 data row; found >1")
        return first

    def _read_column(self, path: str, column_ref: int | str) -> list[str]:
        """Read a column from a legacy comma, tab, or pipe-delimited file."""
        rows: list[str] = []
        # Original VG2 SQL_Get_CSV_List uses Windows-relative .\\file paths.
        # Normalize foreign separators without remapping Windows drives/UNC.
        if "\\" in path and not Path(path).is_absolute():
            windows_path = PureWindowsPath(path)
            if windows_path.drive or windows_path.root:
                raise ValueError(f"Windows absolute CSV-list path is unsupported: {path!r}")
            path = str(Path(*windows_path.parts))
        resolved_path = input_path(path, self.workdir, self.files) if self.workdir is not None else resolve_path(path)

        with resolved_path.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=self._delimiter_for(path))
            header = next(reader, [])
            header_str = [str(h) for h in header]

            if isinstance(column_ref, int):
                idx = column_ref - 1
            else:
                col_lower = [h.lower() for h in header]
                try:
                    idx = col_lower.index(column_ref.lower())
                except ValueError:
                    return []

            seen: dict[str, None] = {}
            for row in reader:
                if [str(v) for v in row] == header_str:
                    continue
                if idx < len(row):
                    val = row[idx]
                    if val not in seen:
                        seen[val] = None
                        rows.append(val)

        return rows

    @staticmethod
    def _delimiter_for(path: str) -> str:
        suffix = Path(path).suffix.casefold()
        if suffix in {".tab", ".hive-tab", ".hive-sequence"}:
            return "\t"
        if suffix == ".asc":
            return "|"
        if suffix == ".plus":
            return "+"
        return ","

    @staticmethod
    def _single_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def sql_get_csv_list(
        self,
        path: str,
        column_ref: int | str,
        lead_in: str,
        chunk_size: int = 1000,
    ) -> str:
        """Return chunked IN-list clause for Oracle-style SQL.

        When there are more values than *chunk_size*, the result is chunked as
        ``(v1..vN) OR <lead_in> (vN+1..)``.
        """
        values = self._read_column(path, column_ref)
        if not values:
            return "('__NO_VALUES__')"

        if chunk_size < 1:
            raise ValueError("SQL_Get_CSV_List chunk_size must be positive")
        chunks = [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]
        parts: list[str] = []
        for i, chunk in enumerate(chunks):
            quoted = ", ".join(self._single_quote(v) for v in chunk)
            parts.append(f"({quoted})")
            if i < len(chunks) - 1:
                parts.append(f"\nOR {lead_in} ")

        return "".join(parts)

    def row_count(self, name: str) -> int:
        """Count data rows (excludes header); 0 if file missing."""
        path = input_path(name, self.workdir, self.files) if self.workdir is not None else resolve_path(name)
        if not path.exists():
            return 0
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=self._delimiter_for(str(path)))
            next(reader, None)  # skip header
            return sum(1 for _ in reader)

    def iter_chunks(
        self, input_name: str, chunk_name: str, chunk_size: int
    ) -> Iterator[Path]:
        """Stream *input_name* in fixed-size chunks, materializing each batch to *chunk_name*.

        Yields the chunk file path once per batch. The header of *input_name* is
        re-written at the top of each chunk so downstream readers can use it.
        """
        if chunk_size <= 0:
            chunk_size = 1
        in_path = input_path(input_name, self.workdir, self.files) if self.workdir is not None else resolve_path(input_name)
        out_path = resolve_path(chunk_name, for_write=True, workdir=self.workdir)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with in_path.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=self._delimiter_for(str(in_path)))
            header = next(reader, None)
            batch: list[list[str]] = []
            for row in reader:
                batch.append(row)
                if len(batch) >= chunk_size:
                    self._write_chunk(out_path, header, batch, files=self.files)
                    yield out_path
                    batch = []
            if batch:
                self._write_chunk(out_path, header, batch, files=self.files)
                yield out_path

    @staticmethod
    def _write_chunk(
        path: Path, header: list[str] | None, rows: list[list[str]], *, files=None
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, delimiter=_CsvIO._delimiter_for(str(path)))
            if header is not None:
                writer.writerow(header)
            writer.writerows(rows)
        if files is not None:
            files.pop(path.resolve(), None)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, name: str, content: Any, header: list[str] | None = None) -> None:
        """Write *content* to a CSV file.

        *content* can be:
        - a DataFrame      -> written with result columns or explicit *header*
        - a list of dicts  -> written via DictWriter (keys as header)
        - a list of lists  -> written via writer (optional *header* for first row)
        - a string         -> written as raw text (no CSV encoding)
        - a Path           -> copied verbatim

        Empty content still creates the file and writes any available header.
        """
        path = resolve_path(name, for_write=True, workdir=self.workdir)
        if self.files is not None:
            self.files.pop(path.resolve(), None)
        path.parent.mkdir(parents=True, exist_ok=True)
        delimiter = self._delimiter_for(str(path))

        if isinstance(content, pandas.DataFrame):
            if header is not None:
                keys = [str(name).casefold() for name in header]
                if len(set(keys)) != len(keys):
                    raise ValueError("CSV output header names collide case-insensitively")
                columns = {}
                for column in content.columns:
                    key = str(column).casefold()
                    if key in columns and key in keys:
                        raise ValueError(f"Ambiguous CSV source columns for {column!r}")
                    columns[key] = column
                content = content.reindex(
                    columns=[
                        columns.get(column.casefold(), column) for column in header
                    ]
                )
                content.columns = header
            content.to_csv(
                path, index=False, encoding="utf-8", sep=delimiter,
                header=len(content.columns) > 0,
            )
            return

        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
            return

        if isinstance(content, Path):
            import shutil

            shutil.copy2(content, path)
            return

        rows = list(content) if content is not None else []
        with path.open("w", newline="", encoding="utf-8") as fh:
            if rows and isinstance(rows[0], dict):
                fieldnames = header if header is not None else list(rows[0].keys())
                writer = csv.DictWriter(
                    fh, fieldnames=fieldnames, extrasaction="ignore", delimiter=delimiter
                )
                writer.writeheader()
                writer.writerows(rows)
            else:
                writer_plain = csv.writer(fh, delimiter=delimiter)
                if header:
                    writer_plain.writerow(header)
                    if rows and rows[0] == header:
                        rows = rows[1:]
                writer_plain.writerows(rows)

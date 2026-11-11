"""Direct filesystem operations with explicit job roots."""

from __future__ import annotations

import csv
from pathlib import Path
import shutil
import subprocess
import sys
import time

from vg2c.runtime.csv_io import _CsvIO
from vg2c.runtime.file_store import MemoryFile, input_path
from vg2c.runtime.values import job_path, resolve_path, substitute


def write_file(
    path, template, *, workdir, values=None, macros=None, vars=None, files=None
):
    if vars is not None:
        macros = {
            **{str(key).upper(): value for key, value in (macros or {}).items()},
            **{str(key).upper(): value for key, value in vars.items()},
        }
    scoped = values is not None or macros is not None
    path = job_path(
        substitute(str(path), values=values, macros=macros) if scoped else path, workdir
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        substitute(template, values=values, macros=macros) if scoped else template,
        encoding="utf-8",
    )
    if files is not None:
        files.pop(path.resolve(), None)


def row_count(path, *, workdir, files=None):
    return _CsvIO(workdir=workdir, files=files).row_count(str(path))


def copy_file(src, dst, *, workdir, recurse=False, files=None):
    src, dst = input_path(src, workdir, files), job_path(dst, workdir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if files is not None:
        files.pop(dst.resolve(), None)
    if isinstance(src, MemoryFile):
        dst.write_bytes(src.read_bytes())
    elif src.is_dir():
        if not recurse:
            raise IsADirectoryError(f"Directory copy requires recurse=True: {src}")
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def rename_file(src, dst, *, workdir, files=None):
    source, destination = (
        job_path(src, workdir).resolve(),
        job_path(dst, workdir).resolve(),
    )
    if files is not None and source in files:
        if source == destination:
            return
        content = files[source]
        if isinstance(content, Path):
            content = content.read_bytes()
        source.unlink(missing_ok=True)
        files[destination] = content
        del files[source]
    else:
        source.replace(destination)
        if files is not None:
            files.pop(destination, None)


def delete_files(paths, *, workdir, recurse=False, files=None):
    for item in paths:
        path = job_path(item, workdir)
        if files is not None:
            files.pop(path.resolve(), None)
            if recurse:
                for name in list(files):
                    if name.is_relative_to(path.resolve()):
                        del files[name]
        if path.is_dir():
            if recurse:
                shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def wait_file(path, timeout=30, *, workdir, interval=5, files=None):
    path = input_path(path, workdir, files)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(interval)
    return path.exists()


def run_program(
    argv, *, workdir, cwd=None, env=None, check=False, exedir=None, files=None
):
    root = job_path(cwd, workdir) if cwd else Path(workdir).resolve()
    if any("@EXEDIR@" in argument for argument in argv):
        if exedir is None:
            raise ValueError("@EXEDIR@ requires an explicit executable root")
        argv = [
            argument.replace("@EXEDIR@", str(Path(exedir).resolve()))
            for argument in argv
        ]
    resolved_argv = []
    for index, argument in enumerate(argv if files else []):
        candidate = input_path(argument, root, files)
        if isinstance(candidate, MemoryFile) and index != 0:
            raise ValueError(
                f"External commands require a disk file: {argument!r}; "
                "explicitly copy the in-memory file to an output path first"
            )
        key = job_path(argument, root).resolve()
        if key in files:
            if not isinstance(candidate, MemoryFile) and key == candidate.resolve():
                raise ValueError(
                    "External commands cannot use a source template as their working file"
                )
            key.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(candidate, MemoryFile):
                key.write_bytes(candidate.read_bytes())
            else:
                shutil.copy2(candidate, key)
            del files[key]
            resolved_argv.append(str(key))
        else:
            resolved_argv.append(argument)
    if resolved_argv:
        argv = resolved_argv
    first = Path(argv[0]) if argv else None
    if first is None:
        raise ValueError("Empty external command")
    if sys.platform != "win32" and first.suffix.lower() in {".bat", ".va", ".exe"}:
        raise RuntimeError(f"Windows command {first} is unsupported on {sys.platform}")
    if "/" in str(first) or "\\" in str(first):
        argv = [str(job_path(first, root)), *argv[1:]]
    root.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        argv,
        cwd=str(root),
        env=env,
        check=check,
        shell=first.suffix.lower() in {".bat", ".va", ".exe"},
    )
    return result.returncode


def smart_append(
    destination: str | Path, source: str | Path, *, workdir, files=None
) -> None:
    """Create or extend *destination* with source data rows exactly once."""
    source_path = input_path(source, workdir, files)
    destination_path = resolve_path(destination, for_write=True, workdir=workdir)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if files is not None and destination_path.resolve() in files:
        existing = input_path(destination, workdir, files)
        if existing.exists():
            destination_path.write_bytes(existing.read_bytes())
        files.pop(destination_path.resolve())
    with source_path.open(newline="", encoding="utf-8", errors="replace") as source_fh:
        reader = csv.reader(source_fh)
        header = next(reader, None)
        if header is None:
            return
        write_header = (
            not destination_path.exists() or destination_path.stat().st_size == 0
        )
        with destination_path.open(
            "w" if write_header else "a", newline="", encoding="utf-8"
        ) as destination_fh:
            writer = csv.writer(destination_fh)
            if write_header:
                writer.writerow(header)
            writer.writerows(reader)

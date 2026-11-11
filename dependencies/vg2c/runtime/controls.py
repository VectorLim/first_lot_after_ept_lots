"""Values for native generated loops; these helpers never execute their bodies."""

from __future__ import annotations

import pandas

from vg2c.runtime.file_store import input_path
from vg2c.runtime.csv_io import _CsvIO
from vg2c.runtime.values import job_path


def for_values(start, end, step, suffix, reverse=None, version=None):
    # Adapted from scripthost-utilities-decompiled/SPSQL3_py/SPFLib/SPFSQL3.py:
    # ForLoopTask.executeTaskCommand (12701-12874), Substitute_Loop_Counter (12917-12926).
    # Keep the supplied-version quirk and signed step tokens; test_control_parity.py.
    if not start.isdigit() or not end.isdigit() or not suffix or int(start) > int(end):
        raise ValueError("Invalid FOR start/end/suffix")
    try:
        delta = int(step)
    except ValueError:
        delta = float(step)

    def row(first, last, stride, counter):
        return {
            f"SPF-START-{suffix}".upper(): str(first),
            f"SPF-END-{suffix}".upper(): str(last),
            f"SPF-STEP-{suffix}".upper(): str(stride),
            f"SPF-STEP-{suffix}-INT".upper(): str(int(stride)),
            f"SPF-LOOP-CTR-{suffix}".upper(): str(counter),
            f"SPF-LOOP-CTR-{suffix}-INT".upper(): str(int(counter)),
        }

    if version is not None:
        yield row(int(start), int(end), int(step), int(start))
        return
    backwards = reverse is None or reverse.upper() == "Y"
    first, last = (int(end), int(start)) if backwards else (int(start), int(end))
    increment = -float(delta) if backwards else float(delta)
    count = 0 if delta == 0 else int((int(end) - int(start)) / delta) + 1
    if delta and last > first + count * float(delta):
        count += 1
    counter = float(first)
    for _ in range(count):
        if (not backwards and counter + increment > last and first != 0) or (
            backwards and counter + increment < last and last != 0
        ):
            increment = last - counter
        yield row(first, last, -increment, counter)
        counter += increment


def site_values(nodes):
    # Adapted from SPFLib/SPFSQL3.py:SiteLoopTask (12965-13039), and
    # SPFUtilities/utils.py:Replace_Special_Chars (5122-5128); keep comma-list order.
    if not nodes:
        raise ValueError("Missing Site Argument")
    for node in nodes.split(","):
        yield {
            "SPF-SITE": node,
            "SPF-SITE-FOR-FILE-NAME": "".join(
                "_" if char in '?/|<>"*{};[].\\/' else char for char in node
            ),
        }


def read_csv(input_file, chunk_file=None, chunk_size=None, *, workdir, files=None):
    """Return the whole CSV as a DataFrame, or chunk-file paths when both chunk args are given."""
    if (chunk_file is None) != (chunk_size is None):
        raise ValueError("chunk_file and chunk_size must be provided together")
    if chunk_file is None:
        source = input_path(input_file, workdir, files)
        with source.open(newline="", encoding="utf-8", errors="replace") as fh:
            return pandas.read_csv(fh, sep=_CsvIO._delimiter_for(str(source)))
    return _csv_chunks(input_file, chunk_file, chunk_size, workdir=workdir, files=files)


def _csv_chunks(input_file, chunk_file, chunk_size, *, workdir, files=None):
    # Adapted from SPFLib/SPFSQL3.py:RunLoopTask (12608-12669); missing input skips.
    source = input_path(input_file, workdir, files)
    if not source.exists():
        return
    if chunk_size <= 0:
        raise ValueError("RUN chunk size must be positive")
    output = job_path(chunk_file, workdir)
    if output.resolve() == job_path(input_file, workdir).resolve():
        raise ValueError("RUN chunk output cannot overwrite its input")
    try:
        yield from _CsvIO(workdir=workdir, files=files).iter_chunks(
            str(source), str(output), chunk_size
        )
    finally:
        output.unlink(missing_ok=True)

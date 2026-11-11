"""Per-invocation WRITE-FILE contents, addressed by their logical filenames."""

from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path

from vg2c.runtime.values import job_path


@dataclass
class MemoryFile:
    path: Path
    content: bytes

    def __str__(self):
        return str(self.path)

    @property
    def name(self):
        return self.path.name

    @property
    def suffix(self):
        return self.path.suffix

    @property
    def stem(self):
        return self.path.stem

    def exists(self):
        return True

    is_file = exists

    def read_bytes(self):
        return self.content

    def read_text(self, encoding="utf-8", errors="strict"):
        with self.open(encoding=encoding, errors=errors) as stream:
            return stream.read()

    def open(self, mode="r", *, encoding="utf-8", errors="strict", newline=None):
        if mode not in {"r", "rb"}:
            raise ValueError("In-memory input files are read-only; use job.update_file")
        stream = BytesIO(self.read_bytes())
        return stream if mode == "rb" else TextIOWrapper(
            stream, encoding=encoding, errors=errors, newline=newline,
        )


def input_path(path, workdir, files=None):
    """Prefer this invocation's WRITE-FILE contents over old disk outputs."""
    resolved = job_path(path, workdir)
    if files is not None and resolved.resolve() in files:
        content = files[resolved.resolve()]
        return content if isinstance(content, Path) else MemoryFile(resolved, content)
    return resolved

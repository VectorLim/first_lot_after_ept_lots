"""DataSyncX email delivery with file inputs resolved from the job root."""

from contextlib import ExitStack
import os
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from vg2c.runtime.values import job_path
from vg2c.runtime.file_store import MemoryFile, input_path
from vg2c.runtime.environment import DEFAULT_TEST_MODE_EXCLUDED


def send_mail(
    to: str,
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    from_addr: str | None = None,
    enabled: bool = True,
    *,
    workdir,
    files=None,
    env_mode=None,
    test_mode_excluded=DEFAULT_TEST_MODE_EXCLUDED,
) -> None:
    mode = os.environ.get("ENV_MODE", "test") if env_mode is None else env_mode
    if mode.strip().lower() not in {"test", "prod"}:
        raise ValueError("ENV_MODE must be test or prod")
    if not enabled or (mode.strip().lower() == "test" and "email" in test_mode_excluded):
        return

    from datasyncx import send_msg

    body_path = input_path(body, workdir, files)
    body_is_file = bool(body) and body_path.is_file()
    content = body_path.read_text(encoding="utf-8", errors="replace") if body_is_file else body
    format_ = "html" if body_is_file and body_path.suffix.lower() in {".htm", ".html"} else "plain"
    files = [input_path(path, workdir, files) for path in attachments or []]
    files = [path for path in files if path.exists()]
    kwargs = {}
    if from_addr:
        kwargs["sender"] = from_addr

    with ExitStack() as stack:
        attachment = files[0] if files else None
        if len(files) == 1 and isinstance(attachment, MemoryFile):
            raise ValueError("Email attachments require a disk file; explicitly copy it to an output path first")
        if len(files) > 1:
            # DataSyncX accepts one file; retain all attachments in one archive.
            tempdir = stack.enter_context(TemporaryDirectory(prefix="vg2c-mail-"))
            attachment = job_path("attachments.zip", tempdir)
            names = [path.name for path in files]
            if len(set(names)) != len(names):
                raise ValueError("Email attachments must have distinct filenames for ZIP delivery")
            with ZipFile(attachment, "w", compression=ZIP_DEFLATED) as archive:
                for path in files:
                    archive.writestr(path.name, path.read_bytes())
        send_msg(
            mail_title=subject,
            mail_content=content,
            receiver=to,
            file=str(attachment) if attachment else None,
            format_=format_,
            **kwargs,
        )

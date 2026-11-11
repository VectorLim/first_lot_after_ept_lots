"""Per-run binding for direct job operations; no compiler or ambient context."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from vg2c.runtime.file_store import input_path
from vg2c.runtime.aed_client import AEDClient
from vg2c.runtime.environment import DEFAULT_TEST_MODE_EXCLUDED, read_environment
from vg2c.runtime.controls import read_csv
from vg2c.runtime.files import (
    copy_file,
    delete_files,
    rename_file,
    row_count,
    run_program,
    smart_append,
    wait_file,
    write_file,
)
from vg2c.runtime.html import _write_atomic, csv_report, render_html
from vg2c.runtime.macros import MacroStore
from vg2c.runtime.mail import send_mail
from vg2c.runtime.nodes import normalize_site, site_facility, site_node
from vg2c.runtime.query import execute_sql
from vg2c.runtime.values import job_path, read_macro_row, snapshot_values, substitute


class JobRuntime:
    """An independent invocation with distinct project assets and output roots."""

    def __init__(
        self,
        assets_root,
        workdir,
        *,
        values: Mapping | None = None,
        environ: Mapping | None = None,
        initial_macros: Mapping | None = None,
        test_mode_excluded=DEFAULT_TEST_MODE_EXCLUDED,
        site: str | None = None,
    ):
        self.assets_root = Path(assets_root).resolve()
        self.workdir = Path(workdir).resolve()
        self.environ = (
            read_environment(self.assets_root) if environ is None else dict(environ)
        )
        site = site or self.environ.get("SITE", "").strip() or None
        self.site = None if site is None else normalize_site(site)
        self.env_mode = self.environ.get("ENV_MODE", "test").strip().lower()
        if self.env_mode not in {"test", "prod"}:
            raise ValueError("ENV_MODE must be test or prod")
        self.environ["ENV_MODE"] = self.env_mode
        self.test_mode_excluded = frozenset(test_mode_excluded)
        self.values = snapshot_values(self.workdir, values=values, environ=self.environ)
        self.macros = MacroStore(values=self.values, initial=initial_macros)
        self.macros["ENV_MODE"] = self.env_mode
        self.files = {}
        self.reports = {}
        self.styles = {}
        self.css_file = None
        self._used_html_reports = set()
        self._aed = None
        self._python_globals = None

    @classmethod
    def from_script(
        cls,
        script_file,
        *,
        site: str | None = None,
        test_mode_excluded=DEFAULT_TEST_MODE_EXCLUDED,
    ):
        """Create one fresh run bound to the generated project's own roots.

        ``site`` sets the default site for ``sql(..., database=...)`` calls;
        when omitted, the ``SITE`` environment variable (or project ``.env``) is used.
        """
        project_dir = Path(script_file).resolve().parent
        return cls(
            assets_root=project_dir,
            workdir=project_dir / "output",
            test_mode_excluded=test_mode_excluded,
            site=site,
        )

    def _resolve_node(self, node, database, site):
        if database is None:
            if site is not None:
                raise ValueError("site= requires database=")
            return node
        if node is not None:
            raise ValueError("Pass either node= or database=, not both")
        site = site or self.site
        if site is None:
            raise ValueError(
                f"No site for database {database!r}: pass site= or set a default "
                "via JobRuntime.from_script(..., site=...)"
            )
        return site_node(database, site)

    def asset_path(self, path) -> Path:
        path = Path(path)
        if not path.is_absolute() and PureWindowsPath(str(path)).is_absolute():
            raise ValueError(
                "Windows absolute asset paths require a mounted Linux path"
            )
        if path.is_absolute():
            return path
        resolved = (self.assets_root / path).resolve()
        if not resolved.is_relative_to(self.assets_root):
            raise ValueError("Asset path must remain inside the generated project")
        return resolved

    def run_python(self, path, **names):
        """Run an embedded-Python asset in a namespace shared across all such assets."""
        if not self.enabled("run_python"):
            return
        asset = self.asset_path(path)
        if self._python_globals is None:
            self._python_globals = {
                "__name__": "__main__",
                "job": self,
                "project_dir": self.assets_root,
                "workdir": self.workdir,
            }
        self._python_globals.update(names, __file__=str(asset))
        exec(
            compile(asset.read_text(encoding="utf-8"), str(asset), "exec"),
            self._python_globals,
        )

    def table_spec(self, path):
        """Load an editable static CSV/SQL option on each invocation."""
        import json

        asset = self.asset_path(path)
        try:
            value = json.loads(asset.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Cannot read SQL table option asset {asset}: {exc}"
            ) from exc
        if not isinstance(value, (list, dict)):
            raise ValueError(
                f"SQL table option asset must contain a list or mapping: {asset}"
            )
        if asset.name.endswith(".crosstab.json"):
            if not isinstance(value, dict):
                raise ValueError(f"SQL crosstab asset must be a mapping: {asset}")
            if (
                not isinstance(value.get("row_keys"), list)
                or not all(isinstance(v, str) and v for v in value["row_keys"])
                or not all(
                    isinstance(value.get(k), str) and value[k]
                    for k in ("header_key", "value_key")
                )
            ):
                raise ValueError(
                    f"SQL crosstab asset {asset} requires row_keys (string list), "
                    "header_key (string), value_key (string)"
                )
        if asset.name.endswith(".header.json") and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            raise ValueError(f"SQL header asset must be a list of strings: {asset}")
        return value

    def report(self, template, *, input_file, output_file=None):
        """Register one editable HTML table without a second report schema.

        HTML-DEFER saves the file/spec at execution time (SPFSQL3.py:20129-20150).
        CSV rows are read later, when HTML-LAYOUT renders it.
        """
        return csv_report(
            input_file,
            output_file=output_file,
            table_template=self.asset_path(template),
        )

    def sql(
        self,
        path,
        *,
        output,
        reader=None,
        inputs=None,
        header=None,
        crosstab=None,
        node=None,
        database=None,
        site=None,
        params=None,
        pivot_columns=None,
        pivot_values=None,
        pivot_duplicate="first",
        pivot_missing="",
        pivot_dot=False,
        pivot_sort=None,
        pivot_header_ref=None,
        pivot_legacy_headers=False,
    ):
        """Run one SQL asset.

        Route with ``database="MARS"|"ARIES"|"OASYS"`` (optionally ``site=`` to
        override the job default), a raw ``node=`` string, or neither for local SQLite.
        """
        node = self._resolve_node(node, database, site)
        if not self.enabled("sql"):
            return
        return execute_sql(
            self.asset_path(path),
            reader=reader,
            environ=self.environ,
            output=output,
            workdir=self.workdir,
            values=self.values,
            macros=self.macros,
            files=self.files,
            inputs=inputs,
            header=header,
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

    def define_css(self, output, asset):
        """Publish an editable stylesheet when HTML-RUN CSS executes.

        SPFSQL3.py:20095-20098 and SPFUtilities/utils.py:9028-9047.
        """
        if not self.enabled("define_css"):
            return
        source = self.asset_path(asset)
        target = job_path(
            substitute(str(output), values=self.values, macros=self.macros),
            self.workdir,
        ).resolve()
        if not target.is_relative_to(self.workdir) or target == source:
            raise ValueError(f"CSS output must stay inside the job workdir: {output!r}")
        _write_atomic(target, source.read_text(encoding="utf-8"))
        self.files.pop(target.resolve(), None)
        self.css_file = str(target)
        return target

    def delete_html(self):
        """Remove only used deferred reports (SPFSQL3.py:20244-20250)."""
        if not self.enabled("delete_html"):
            return
        for report_id in self._used_html_reports:
            self.reports.pop(report_id, None)
        self._used_html_reports.clear()

    def html(
        self,
        path,
        *,
        output,
        values=None,
        instance=None,
        css_file=None,
        embed_css=False,
        reports=None,
    ):
        if not self.enabled("html"):
            return
        slots = self.values if values is None else {**self.values, **values}
        selected = self.css_file if css_file is None else css_file
        if css_file is not None and not Path(str(css_file)).is_absolute():
            # Prefer an executed HTML-RUN output, then a project-relative
            # editable asset; render_html still supports template siblings.
            resolved = substitute(str(css_file), values=slots, macros=self.macros)
            candidate = input_path(resolved, self.workdir, self.files)
            if candidate.is_file():
                selected = str(candidate)
            else:
                asset = self.asset_path(resolved)
                if asset.is_file():
                    selected = str(asset)
        return render_html(
            self.asset_path(path),
            output=output,
            workdir=self.workdir,
            reports=self.reports if reports is None else reports,
            values=slots,
            macros=self.macros,
            styles=self.styles,
            css_file=selected,
            embed_css=embed_css,
            instance=instance,
            used_reports=self._used_html_reports if reports is None else None,
            files=self.files,
        )

    def write_file(self, path, template, *, vars=None):
        if not self.enabled("write_file"):
            return
        return write_file(
            path,
            template,
            workdir=self.workdir,
            values=self.values,
            macros=self.macros,
            vars=vars,
            files=self.files,
        )

    def load_file(self, path, asset):
        """Activate an editable WRITE-FILE asset without changing it."""
        if not self.enabled("load_file"):
            return
        source = self.asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        self.files[job_path(path, self.workdir).resolve()] = source

    def update_file(self, path, asset):
        """Expand the original template afresh at its source execution point."""
        if not self.enabled("update_file"):
            return
        content = self.asset_path(asset).read_text(encoding="utf-8")
        content = substitute(content, values=self.values, macros=self.macros)
        self.files[job_path(path, self.workdir).resolve()] = content.encode("utf-8")

    def read_file(self, path):
        """Read this run's content by its logical filename."""
        return input_path(path, self.workdir, self.files).read_text(encoding="utf-8")

    def row_count(self, path):
        return row_count(path, workdir=self.workdir, files=self.files)

    def read_macro_row(self, path):
        return read_macro_row(
            self.macros.substitute(str(path)), workdir=self.workdir, files=self.files
        )

    def read_csv(self, input_file, chunk_file=None, chunk_size=None):
        return read_csv(
            self.macros.substitute(str(input_file)),
            None if chunk_file is None else self.macros.substitute(str(chunk_file)),
            chunk_size,
            workdir=self.workdir,
            files=self.files,
        )

    def copy_file(self, src, dst, *, recurse=False):
        if not self.enabled("copy_file"):
            return
        return copy_file(
            src, dst, workdir=self.workdir, recurse=recurse, files=self.files
        )

    def rename_file(self, src, dst):
        if not self.enabled("rename_file"):
            return
        return rename_file(src, dst, workdir=self.workdir, files=self.files)

    def delete_files(self, paths, *, recurse=False):
        if not self.enabled("delete_files"):
            return
        return delete_files(
            paths, workdir=self.workdir, recurse=recurse, files=self.files
        )

    def wait_file(self, path, timeout=30, *, interval=5):
        if not self.enabled("wait_file"):
            return
        return wait_file(
            path, timeout, workdir=self.workdir, interval=interval, files=self.files
        )

    def run_program(self, argv, *, cwd=None, env=None, check=False, exedir=None):
        if not self.enabled("run_program"):
            return
        return run_program(
            argv,
            workdir=self.workdir,
            cwd=cwd,
            env=env,
            check=check,
            exedir=exedir,
            files=self.files,
        )

    def smart_append(self, destination, source):
        if not self.enabled("smart_append"):
            return
        return smart_append(destination, source, workdir=self.workdir, files=self.files)

    def send_mail(
        self, to, subject, body, attachments=None, from_addr=None, enabled=True
    ):
        if not self.enabled("email"):
            return
        return send_mail(
            to,
            subject,
            body,
            attachments,
            from_addr,
            enabled,
            workdir=self.workdir,
            env_mode=self.env_mode,
            test_mode_excluded=self.test_mode_excluded,
            files=self.files,
        )

    def enabled(self, name):
        """False when `name` is in TEST_MODE_EXCLUDED and ENV_MODE is test."""
        return self.env_mode == "prod" or name not in self.test_mode_excluded

    def submit_mms(self, src, dst, *, recurse=False):
        if not self.enabled("mms"):
            return
        return copy_file(
            src, dst, workdir=self.workdir, recurse=recurse, files=self.files
        )

    @property
    def aed(self) -> AEDClient | None:
        if not self.enabled("aed"):
            return
        if self._aed:
            return self._aed
        facility = self.macros["FACILITY"] or (
            site_facility(self.site) if self.site else None
        )
        if not facility:
            raise ValueError(
                "No AED facility: set the FACILITY macro or a default site "
                "(from_script(..., site=...) or SITE env var)"
            )
        self._aed = AEDClient(facility, environ=self.environ)
        return self._aed

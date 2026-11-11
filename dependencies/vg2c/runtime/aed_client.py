"""Thin, call-scoped wrapper over the upstream AEDAPIClient (vg2c.runtime.aed_api)."""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

from vg2c.runtime.environment import AED_ENVIRONMENT_LOCK

_BUNDLED_CA = Path(__file__).with_name("IntelSHA256RootCA-Base64.crt")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")
_SECRET_FILES = ("key.key", "config_new.json")


def identifier(value, name):
    if (
        not isinstance(value, str)
        or not _IDENTIFIER.fullmatch(value)
        or value in {".", ".."}
    ):
        raise ValueError(f"Invalid {name}: expected a nonempty identifier")
    return value


def _secret_files(directory):
    """Encrypted API_KEY/API_CONFIG pair stored as upstream secret files."""
    path = Path(directory)
    if not path.is_absolute():
        raise ValueError("AED_SECRETS_DIR must be an absolute path")
    try:
        return [
            (path / name).read_text(encoding="utf-8").strip() for name in _SECRET_FILES
        ]
    except OSError:
        raise ValueError("AED secret files are unavailable") from None


def _call_environment(environ):
    """Process variables the upstream client reads, resolved from a job environment."""
    values = {
        key: environ.get(key) or None for key in ("X_API_KEY", "API_KEY", "API_CONFIG")
    }
    if not any(values.values()) and environ.get("AED_SECRETS_DIR"):
        values["API_KEY"], values["API_CONFIG"] = _secret_files(
            environ["AED_SECRETS_DIR"]
        )
    values["REQUESTS_CA_BUNDLE"] = environ.get("REQUESTS_CA_BUNDLE") or str(_BUNDLED_CA)
    values["NO_PROXY"] = environ.get("NO_PROXY") or environ.get("no_proxy")
    if os.name != "nt":  # Windows environment keys are case-insensitive.
        values["no_proxy"] = environ.get("no_proxy")
    return values


def _set_environ(values):
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@contextlib.contextmanager
def _scoped_environ(values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        _set_environ(values)
        yield
    finally:
        _set_environ(previous)


def _installed(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _upstream_client(facility):
    ca = Path(os.environ["REQUESTS_CA_BUNDLE"])
    if not ca.is_absolute() or not ca.is_file():
        raise ValueError("REQUESTS_CA_BUNDLE must name an existing absolute CA bundle")
    if _installed("aed-client-winauth"):
        raise RuntimeError("Use an isolated AED environment without aed-client-winauth")
    if _installed("aed-client-apikey") != "6.2.0":
        raise RuntimeError(
            "Install the approved aed-client-apikey 6.2.0 wheel and vg2c[aed]"
        )
    # The SDK parses host arguments at import.
    argv, sys.argv = sys.argv, sys.argv[:1] or ["vg2c"]
    try:
        from vg2c.runtime.aed_api import AEDAPIClient
    finally:
        sys.argv = argv
    # The SDK context is process-global; constructing reloads it for this facility.
    return AEDAPIClient(facility)


class AEDClient:
    """Sequential lot operations; test-mode gating is owned by JobRuntime.aed.

    client_factory(facility) supplies an AEDAPIClient-shaped fake for offline tests.
    """

    def __init__(self, facility, *, environ=None):
        self.facility = identifier(facility, "facility")
        self.environ = environ
        self._client_factory = _upstream_client

    def _call(self, method, *identifiers, **text):
        """Invoke `method` with identifier then free-text arguments, in that order."""
        for value in identifiers:
            identifier(value, f"{method} argument")
        if not all(isinstance(value, str) for value in text.values()):
            raise ValueError(f"AED {method} text arguments must be strings")
        # The SDK does not URL-encode path arguments.
        arguments = [quote(value, safe="") for value in (*identifiers, *text.values())]
        environ = os.environ if self.environ is None else self.environ
        try:
            # The SDK prints URLs and transaction context; keep them out of job logs.
            with (
                AED_ENVIRONMENT_LOCK,
                _scoped_environ(_call_environment(environ)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                client = self._client_factory(self.facility)
                result = getattr(client, method)(*arguments)
        except (ValueError, RuntimeError):
            raise
        except Exception as err:
            # Upstream errors can embed response bodies; report only the type.
            raise RuntimeError(
                f"AED {method} request failed ({type(err).__name__})"
            ) from None
        if not isinstance(result, dict) or result.get("Success") is not True:
            raise RuntimeError(
                f"AED {method} returned an unsuccessful or malformed response"
            )
        return result

    def test_connection(self, lot):
        """Read a known lot; return True or raise.

        Verifies connectivity, authentication and lot read access, not write permissions.
        """
        self._call("lot_status", lot)
        return True

    def lot_status(self, lot):
        return self._call("lot_status", lot)

    def lot_hold(self, lotid, operation, holdcode, comment):
        return self._call("lot_hold", lotid, operation, holdcode, comment=comment)

    def lot_release(self, lotid, operation, comment):
        return self._call("lot_release", lotid, operation, comment=comment)

    def lot_set_attr(self, lotid, attributeid, attributevalue):
        return self._call(
            "lot_set_attr", lotid, attributeid, attributevalue=attributevalue
        )

    def lot_get_attr(self, lotid, attributeid):
        return self._call("lot_get_attr", lotid, attributeid)

    def lot_contain(self, lotid, operation, reference_number):
        return self._call(
            "lot_contain", lotid, operation, reference_number=reference_number
        )

    def lot_uncontain(self, lotid, operation, reference_number, comment):
        return self._call(
            "lot_uncontain",
            lotid,
            operation,
            reference_number=reference_number,
            comment=comment,
        )

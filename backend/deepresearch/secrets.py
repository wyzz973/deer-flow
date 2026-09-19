"""Credentials referenced by research configuration.

Configuration stores references, never values: ``$NAME`` reads an environment
variable of the Gateway process and ``secret:NAME`` reads a value saved on the
settings page. Saved values are write-only through the API. Every resolved
value is also registered for redaction in traces and audits.

A request may carry its own credentials (``request_secret_headers`` maps a
header to a credential name). Those values belong to one user and one request:
they take precedence over the saved value for the same name and are never
stored, cached process-wide, or shared between requests.
"""

from __future__ import annotations

import os
import re
import threading

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
# A reference inside a longer value: "Bearer ${KB_TOKEN}", "sid=${secret:kb-cookie}; lang=zh".
INTERPOLATION = re.compile(r"\$\{(secret:[A-Za-z0-9_.-]{1,80}|[A-Za-z_][A-Za-z0-9_]*)\}")


class SecretBox:
    def __init__(self):
        self._saved: dict[str, str] = {}
        self._lock = threading.Lock()

    def load(self, saved: dict[str, str]):
        with self._lock:
            self._saved = dict(saved)

    def set(self, name: str, value: str | None):
        with self._lock:
            if value:
                self._saved[name] = value
            else:
                self._saved.pop(name, None)

    def names(self):
        with self._lock:
            return sorted(self._saved)

    def resolve(self, ref: str | None, request: dict | None = None) -> str | None:
        if not ref:
            return None
        if ref.startswith("$"):
            return os.environ.get(ref[1:]) or None
        if ref.startswith("secret:"):
            name = ref[len("secret:") :]
            supplied = (request or {}).get(name)
            if isinstance(supplied, str) and supplied:
                return supplied
            with self._lock:
                return self._saved.get(name)
        return None

    def status(self, ref: str | None) -> str:
        if not ref:
            return "none"
        return "set" if self.resolve(ref) else "missing"

    def expand(self, value, request: dict | None = None):
        """Resolve references inside header or environment mappings."""
        if isinstance(value, str) and value.startswith(("$", "secret:")) and not value.startswith("${"):
            return self.resolve(value, request) or ""
        if isinstance(value, str):
            # "${NAME}" is an environment variable and "${secret:NAME}" a saved or
            # per-request secret, so a header can carry a scheme or a cookie name
            # around the credential: "Bearer ${secret:kb-token}".
            return INTERPOLATION.sub(lambda match: self.resolve(match.group(1) if match.group(1).startswith("secret:") else "$" + match.group(1), request) or "", value)
        if isinstance(value, dict):
            return {key: self.expand(item, request) for key, item in value.items()}
        if isinstance(value, list):
            return [self.expand(item, request) for item in value]
        return value

    def values(self):
        """Every saved value, for redaction. Environment values are added per use."""
        with self._lock:
            return tuple(value for value in self._saved.values() if value)


SECRETS = SecretBox()


def references(value):
    """Credential references anywhere in a configuration fragment."""
    found = set()
    if isinstance(value, str) and value.startswith(("$", "secret:")) and not value.startswith("${"):
        found.add(value)
    elif isinstance(value, str):
        # Interpolated credentials are redacted like whole-value references.
        found |= {name if name.startswith("secret:") else "$" + name for name in INTERPOLATION.findall(value)}
    elif isinstance(value, dict):
        for item in value.values():
            found |= references(item)
    elif isinstance(value, list):
        for item in value:
            found |= references(item)
    return found


def resolved_values(settings):
    """Resolved credential values used by a settings object, for redaction."""
    refs = references(settings.model_dump(mode="json", include={"models", "sources", "mcp_servers"}))
    return tuple(value for value in (SECRETS.resolve(ref) for ref in refs) if value)


def trace_secrets(settings, context=None):
    """Values a trace or audit must never contain: request credentials and configured keys."""
    supplied = tuple(value for value in ((context or {}).get("secrets") or {}).values() if isinstance(value, str) and value)
    return supplied + resolved_values(settings) + SECRETS.values()

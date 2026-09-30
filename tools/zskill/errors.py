"""Typed, controlled failures. Every user-facing failure carries a machine-readable `code` plus file/path
information, and maps to a CLI exit status (Protocol v0.2 section 9):

  2  malformed input or validation error          (RegistryError)
  1  a valid request that cannot be satisfied     (UnsatisfiedRequest), e.g. an unresolved reference
"""
from __future__ import annotations


class RegistryError(Exception):
    exit_code = 2

    def __init__(self, message: str, *, code: str = "error", file: str | None = None,
                 path: list | None = None, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.message, self.code, self.file, self.path, self.line, self.column = message, code, file, path, line, column

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict:
        return {"code": self.code, "file": self.file, "path": label_path(self.path or []), "line": self.line,
                "column": self.column, "message": self.message}


class YamlError(RegistryError):
    """Input rejected by the Protocol v0.2 JSON-compatible YAML subset (or unreadable/malformed YAML)."""


class BundleError(RegistryError):
    """A bundle cannot be hashed: symlinks, special files, disallowed or colliding paths, invalid payload text."""


class CanonicalizationError(RegistryError, ValueError):
    """A value cannot be represented in RFC 8785 canonical JSON under the v0.2 restrictions."""


class UnsatisfiedRequest(RegistryError):
    """The request is well formed but cannot be satisfied (unknown artifact, unresolved reference, ...)."""
    exit_code = 1


def label_path(path: list) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
    return out or "<document root>"

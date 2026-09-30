"""Protocol v0.2 manifest input: a JSON-compatible YAML subset.

Accepted: one YAML document whose mappings have *string* keys, sequences, and scalars that resolve as
  null    ~ null Null NULL (or empty)
  bool    true True TRUE false False FALSE          (yes/no/on/off/y/n are STRINGS)
  int     -?(0|[1-9][0-9]*), within +/-(2^53-1)
  float   JSON number grammar with a fraction or exponent, finite
  string  everything else (quoted or plain); timestamps stay strings
Rejected, each with file, path, line/column and a machine-readable code:
  duplicate-key, alias, anchor, merge-key, non-string-key, multiple-documents, unsupported-tag, unsafe-integer,
  non-finite, invalid-utf8, bom, nul, lone-surrogate, ambiguous-scalar (numeric-looking YAML forms outside the JSON
  number grammar such as 010, +1, .5, 0x1F, .inf; quote them to keep a string), invalid-scalar, syntax,
  invalid-character, nesting-too-deep.
PyYAML is used only for scanning, parsing and composing; values are built here, never by PyYAML constructors.
"""
from __future__ import annotations

import math
import re

import yaml
from yaml.composer import Composer
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import BaseResolver
from yaml.scanner import Scanner

from .errors import YamlError

SAFE_INT = 2**53 - 1
TAG = "tag:yaml.org,2002:"
T_STR, T_INT, T_FLOAT, T_BOOL, T_NULL, T_SEQ, T_MAP = (TAG + n for n in ("str", "int", "float", "bool", "null", "seq", "map"))
CORE_TAGS = {T_STR, T_INT, T_FLOAT, T_BOOL, T_NULL, T_SEQ, T_MAP}

_NULL = re.compile(r"^(~|null|Null|NULL)?$")
_BOOL = re.compile(r"^(true|True|TRUE|false|False|FALSE)$")
_INT = re.compile(r"^-?(0|[1-9][0-9]*)$")
_FLOAT = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
# Numeric-looking in YAML 1.2 core (or explicit non-decimal forms) but outside the JSON number grammar.
_AMBIGUOUS = [re.compile(p) for p in (
    r"^[-+]?[0-9]+$", r"^[-+]?(\.[0-9]+|[0-9]+\.[0-9]*)([eE][-+]?[0-9]+)?$", r"^[-+]?[0-9]+[eE][-+]?[0-9]+$",
    r"^0[xX][0-9a-fA-F]+$", r"^0[oO][0-7]+$", r"^0[bB][01]+$", r"^[-+]?\.(inf|Inf|INF)$", r"^\.(nan|NaN|NAN)$")]


class _Reject(Exception):
    def __init__(self, code, message, mark=None):
        super().__init__(message)
        self.code, self.message, self.mark = code, message, mark


class _Composer(Composer):
    """Rejects anchors and aliases at compose time."""

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise _Reject("alias", "aliases (*name) are not allowed", self.peek_event().start_mark)
        ev = self.peek_event()
        if getattr(ev, "anchor", None) is not None:
            raise _Reject("anchor", f"anchors (&{ev.anchor}) are not allowed", ev.start_mark)
        return super().compose_node(parent, index)


class _Resolver(BaseResolver):
    """Implicit typing for plain scalars per the subset; everything quoted or unmatched is a string."""

    def resolve(self, kind, value, implicit):
        if kind is ScalarNode:
            if implicit[0]:  # plain scalar
                if _NULL.match(value):
                    return T_NULL
                if _BOOL.match(value):
                    return T_BOOL
                if _INT.match(value):
                    return T_INT
                if _FLOAT.match(value):
                    return T_FLOAT
                if any(p.match(value) for p in _AMBIGUOUS):
                    return "zskill:ambiguous"
            return T_STR
        return T_SEQ if kind is SequenceNode else T_MAP


class _Loader(Reader, Scanner, Parser, _Composer, _Resolver):
    def __init__(self, stream):
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        BaseResolver.__init__(self)


def _scalar(node: ScalarNode, path: list):
    tag, v = node.tag, node.value
    where = (node.start_mark.line + 1, node.start_mark.column + 1)
    if tag == "zskill:ambiguous":
        raise _Reject("ambiguous-scalar", f"plain scalar {v!r} looks numeric but is outside the JSON number grammar; "
                      "quote it to keep a string", node.start_mark)
    if tag == T_STR:
        _check_string(v, node.start_mark)
        return v
    if tag == T_NULL:
        if not _NULL.match(v):
            raise _Reject("invalid-scalar", f"{v!r} is not a null value", node.start_mark)
        return None
    if tag == T_BOOL:
        if not _BOOL.match(v):
            raise _Reject("invalid-scalar", f"{v!r} is not a boolean (only true/false forms; yes/no/on/off are strings)", node.start_mark)
        return v.lower() == "true"
    if tag == T_INT:
        if not _INT.match(v):
            raise _Reject("invalid-scalar", f"{v!r} is not a decimal integer", node.start_mark)
        n = int(v)
        if abs(n) > SAFE_INT:
            raise _Reject("unsafe-integer", f"integer {v} is outside the safe range +/-(2^53-1)", node.start_mark)
        return n
    if tag == T_FLOAT:
        if not (_FLOAT.match(v) or _INT.match(v)):
            raise _Reject("invalid-scalar", f"{v!r} is not a JSON-grammar number", node.start_mark)
        f = float(v)
        if not math.isfinite(f):
            raise _Reject("non-finite", f"number {v!r} is not finite", node.start_mark)
        if _INT.match(v) and abs(int(v)) > SAFE_INT:
            raise _Reject("unsafe-integer", f"integer {v} is outside the safe range +/-(2^53-1)", node.start_mark)
        return f
    raise _Reject("unsupported-tag", f"tag {tag!r} is not supported", node.start_mark)


def _check_string(s: str, mark):
    if "\x00" in s:
        raise _Reject("nul", "string contains a NUL character", mark)
    for ch in s:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise _Reject("lone-surrogate", "string contains a lone surrogate", mark)


def _convert(node, path: list):
    if node.tag not in CORE_TAGS and node.tag != "zskill:ambiguous":
        raise _Reject("unsupported-tag", f"tag {node.tag!r} is not supported", node.start_mark)
    if isinstance(node, ScalarNode):
        return _scalar(node, path)
    if isinstance(node, SequenceNode):
        if node.tag != T_SEQ:
            raise _Reject("unsupported-tag", f"tag {node.tag!r} on a sequence is not supported", node.start_mark)
        return [_convert(child, [*path, i]) for i, child in enumerate(node.value)]
    if node.tag != T_MAP:
        raise _Reject("unsupported-tag", f"tag {node.tag!r} on a mapping is not supported", node.start_mark)
    out: dict = {}
    first: dict = {}
    for knode, vnode in node.value:
        if isinstance(knode, ScalarNode) and knode.style is None and knode.value == "<<":
            raise _Reject("merge-key", "merge keys (<<) are not allowed", knode.start_mark)
        if not isinstance(knode, ScalarNode) or knode.tag != T_STR:
            shown = knode.value if isinstance(knode, ScalarNode) else "<complex key>"
            raise _Reject("non-string-key", f"mapping key {shown!r} is not a string (quote it)", knode.start_mark)
        _check_string(knode.value, knode.start_mark)
        key = knode.value
        if key in out:
            raise _Reject("duplicate-key", f"duplicate mapping key {key!r} (first defined at line {first[key].line + 1})",
                          knode.start_mark) from None
        first[key] = knode.start_mark
        out[key] = _convert(vnode, [*path, key])
    return out


def _path_of(root, target_mark) -> list:
    """Best-effort key path of the node starting at `target_mark`, for diagnostics."""
    def walk(node, path):
        if node.start_mark is target_mark or (node.start_mark.index == target_mark.index and node.start_mark.line == target_mark.line):
            return path
        if isinstance(node, SequenceNode):
            for i, c in enumerate(node.value):
                r = walk(c, [*path, i])
                if r is not None:
                    return r
        elif isinstance(node, MappingNode):
            for k, v in node.value:
                if k.start_mark.index == target_mark.index:
                    return [*path, k.value] if isinstance(k, ScalarNode) else path
                r = walk(v, [*path, k.value if isinstance(k, ScalarNode) else "?"])
                if r is not None:
                    return r
        return None
    try:
        return walk(root, []) or []
    except Exception:  # noqa: BLE001 - diagnostics only
        return []


def load_yaml_text(text: str, source: str):
    """Parse one YAML document under the v0.2 subset. Raises YamlError (code, file, path, line, column)."""
    def fail(code, message, mark=None, path=None):
        line = mark.line + 1 if mark is not None else None
        col = mark.column + 1 if mark is not None else None
        where = f" (line {line}, column {col})" if line else ""
        plabel = ""
        if path:
            from .errors import label_path
            plabel = f"{label_path(path)}: "
        raise YamlError(f"{source}: {plabel}[{code}] {message}{where}", code=code, file=source, path=path or [],
                        line=line, column=col)

    if text.startswith("\ufeff"):
        fail("bom", "a byte order mark is not allowed")
    if "\x00" in text:
        i = text.index("\x00")
        fail("nul", "NUL characters are not allowed", type("M", (), {"line": text.count("\n", 0, i), "column": i - (text.rfind("\n", 0, i) + 1)})())
    loader = _Loader(text)
    root = None
    try:
        try:
            root = loader.get_single_node()
        except _Reject as r:
            fail(r.code, r.message, r.mark)
        except yaml.composer.ComposerError as e:
            if "single document" in str(e):
                fail("multiple-documents", "only one YAML document is allowed per file", getattr(e, "problem_mark", None))
            fail("syntax", str(e).splitlines()[0], getattr(e, "problem_mark", None))
        except yaml.reader.ReaderError as e:
            fail("invalid-character", f"unacceptable character #x{e.character:04x}" if isinstance(e.character, int) else str(e))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            msg = (getattr(e, "problem", None) or str(e)).strip().splitlines()[0]
            fail("syntax", msg, mark)
        if root is None:
            return None
        try:
            return _convert(root, [])
        except _Reject as r:
            fail(r.code, r.message, r.mark, _path_of(root, r.mark) if r.mark is not None else None)
    except RecursionError:
        fail("nesting-too-deep", "document is nested too deeply")
    finally:
        loader.dispose()


def load_yaml(path) -> object:
    from pathlib import Path
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise YamlError(f"{p}: [unreadable] cannot read file ({e.strerror or e})", code="unreadable", file=str(p)) from e
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        line = raw.count(b"\n", 0, e.start) + 1
        raise YamlError(f"{p}: [invalid-utf8] not valid UTF-8 ({e.reason} at byte {e.start}, line {line})",
                        code="invalid-utf8", file=str(p), line=line) from e
    return load_yaml_text(text, str(p))

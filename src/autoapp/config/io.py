"""Round-trip YAML helpers that keep the user's comments and hand edits.

``update_section`` rewrites exactly one top-level key by splicing text, so every
line outside that section stays byte-identical (design decision 2).
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(path: Path) -> CommentedMap:
    """Load ``path`` round-trip; an empty map if the file is missing or empty."""
    path = Path(path)
    if not path.exists():
        return CommentedMap()
    data = _yaml().load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, CommentedMap) else CommentedMap()


def to_plain(value: Any) -> Any:
    """Convert ruamel containers/scalars into plain dict/list/str/int/... values."""
    if isinstance(value, Mapping):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _dump_text(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_yaml(path: Path, data: Any) -> None:
    """Write ``data`` as YAML atomically (temp file in the same directory + replace)."""
    _atomic_write(Path(path), _dump_text(data))


def _is_seq(v: Any) -> bool:
    return isinstance(v, Sequence) and not isinstance(v, (str, bytes))


def _merge(old: Any, new: Any) -> Any:
    """Merge ``new`` into ``old`` in place where shapes match, keeping inner comments."""
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for k in [k for k in old if k not in new]:
            del old[k]
        for k, v in new.items():
            old[k] = _merge(old[k], v) if k in old else v
        return old
    if _is_seq(old) and _is_seq(new) and hasattr(old, "append"):
        for i, v in enumerate(new):
            if i < len(old):
                old[i] = _merge(old[i], v)
            else:
                old.append(v)
        del old[len(new) :]
        return old
    return new


def _section_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of the top-level ``key`` and its indented body."""
    pat = re.compile(rf"""^(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*:(\s|$)""")
    start = next((i for i, ln in enumerate(lines) if pat.match(ln)), None)
    if start is None:
        return None
    last = start
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        if ln[0] in " \t" or ln.startswith("- ") or ln.rstrip() == "-":
            last = j
        elif ln.startswith("#"):
            continue
        else:
            break
    return start, last + 1


def update_section(path: Path, key: str, value: Any) -> None:
    """Replace the top-level ``key`` in ``path`` with ``value``.

    Everything outside the section (other keys, comments, blank lines) is kept
    byte-for-byte. Comments inside the section survive where the shape is unchanged.
    A missing key is appended at the end; a missing file is created.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines(keepends=True)
    span = _section_span(lines, key)
    if span is None:
        new_block = _dump_text({key: value})
        if text and not text.endswith("\n"):
            text += "\n"
        _atomic_write(path, text + new_block)
        return
    start, end = span
    snippet = "".join(lines[start:end])
    old = _yaml().load(snippet)
    if isinstance(old, CommentedMap) and key in old:
        old[key] = _merge(old[key], value)
        new_block = _dump_text(old)
    else:
        new_block = _dump_text({key: value})
    if not new_block.endswith("\n"):
        new_block += "\n"
    _atomic_write(path, "".join(lines[:start]) + new_block + "".join(lines[end:]))

"""Sandboxed workspace for OpenSCAD model files.

All file operations exposed by the MCP server are confined to a single
workspace directory. This module resolves and validates every path so that
callers cannot escape the workspace via absolute paths, ``..`` segments, or
symlinks.
"""

from __future__ import annotations

import os
from pathlib import Path

# Extensions we treat as editable OpenSCAD source.
SCAD_SUFFIXES = {".scad"}

# Extensions the server is allowed to write/export as build artifacts.
EXPORT_SUFFIXES = {
    ".stl",
    ".3mf",
    ".off",
    ".amf",
    ".csg",
    ".dxf",
    ".svg",
    ".pdf",
    ".wrl",
    ".png",
}


class SandboxError(Exception):
    """Raised when a requested path falls outside the sandbox or is invalid."""


class Sandbox:
    """Confines all file access to a single workspace directory."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path handling ---------------------------------------------------

    def resolve(self, relative_path: str) -> Path:
        """Resolve *relative_path* against the workspace root.

        Raises :class:`SandboxError` if the resulting path would escape the
        workspace (via ``..``, an absolute path, or a symlink target).
        """
        if not relative_path or not str(relative_path).strip():
            raise SandboxError("Path must not be empty.")

        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise SandboxError(
                f"Absolute paths are not allowed: {relative_path!r}. "
                "Use a path relative to the workspace root."
            )

        # Resolve against root; strict=False so not-yet-created files work.
        resolved = (self.root / candidate).resolve()

        # Confirm the resolved path is inside the workspace root.
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxError(
                f"Path {relative_path!r} escapes the workspace sandbox."
            )
        return resolved

    def relative(self, path: Path) -> str:
        """Return *path* as a POSIX string relative to the workspace root."""
        return path.resolve().relative_to(self.root).as_posix()

    # -- validation ------------------------------------------------------

    def require_scad(self, relative_path: str) -> Path:
        path = self.resolve(relative_path)
        if path.suffix.lower() not in SCAD_SUFFIXES:
            raise SandboxError(
                f"Expected a .scad file, got {relative_path!r}."
            )
        return path

    def validate_export_target(self, relative_path: str) -> Path:
        path = self.resolve(relative_path)
        suffix = path.suffix.lower()
        if suffix not in EXPORT_SUFFIXES:
            allowed = ", ".join(sorted(EXPORT_SUFFIXES))
            raise SandboxError(
                f"Unsupported export extension {suffix!r}. Allowed: {allowed}."
            )
        return path

    # -- file operations -------------------------------------------------

    def list_scad(self) -> list[str]:
        """Return all .scad files in the workspace, relative and sorted."""
        return sorted(
            self.relative(p)
            for p in self.root.rglob("*.scad")
            if p.is_file()
        )

    def read(self, relative_path: str) -> str:
        path = self.require_scad(relative_path)
        if not path.is_file():
            raise SandboxError(f"File not found: {relative_path!r}.")
        return path.read_text(encoding="utf-8")

    def write(self, relative_path: str, content: str) -> Path:
        path = self.require_scad(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def exists(self, relative_path: str) -> bool:
        try:
            return self.resolve(relative_path).is_file()
        except SandboxError:
            return False

"""MCP server exposing OpenSCAD authoring, rendering, and export tools.

Configuration (environment variables):
    OPENSCAD_MCP_WORKSPACE  Directory that holds all .scad files and renders.
                            Defaults to ./workspace under the current dir.
    OPENSCAD_MCP_BINARY     Path to the openscad binary (else found on PATH).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .imaging import tile_images
from .openscad import (
    DEFAULT_VIEWS,
    VIEW_PRESETS,
    OpenSCAD,
    OpenSCADError,
    filter_noise,
)
from .sandbox import Sandbox, SandboxError

# -- configuration -------------------------------------------------------

_workspace_env = os.environ.get("OPENSCAD_MCP_WORKSPACE")
WORKSPACE = Path(_workspace_env).expanduser() if _workspace_env else Path.cwd() / "workspace"
_binary = os.environ.get("OPENSCAD_MCP_BINARY")

sandbox = Sandbox(WORKSPACE)
RENDER_DIR = "renders"

# When truthy, every render is opened in the OS image viewer (Preview on macOS).
# Useful because some MCP hosts (e.g. Claude Desktop) deliver the image to the
# model but do not display it to the user in the chat UI.
_AUTO_OPEN = os.environ.get("OPENSCAD_MCP_AUTO_OPEN", "").strip().lower() in (
    "1", "true", "yes", "on",
)

mcp = FastMCP("openscad-mcp")


def _openscad() -> OpenSCAD:
    """Instantiate the wrapper lazily so a missing binary surfaces per-call."""
    return OpenSCAD(binary=_binary)


def _maybe_open(path: Path, force: bool = False) -> None:
    """Open *path* in the OS image viewer if auto-open is enabled.

    Fire-and-forget and fully guarded — never blocks or breaks the tool call.
    """
    if not (_AUTO_OPEN or force):
        return
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif sys.platform.startswith("linux"):
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass  # viewer is a convenience; never fail the render over it


def _summarize(result: Any, success_msg: str) -> str:
    """Render an OpenSCAD RunResult into a compact human-readable string."""
    detail = filter_noise(result.messages)
    if result.ok:
        return f"{success_msg}\n\n{detail}" if detail else success_msg
    return (
        f"OpenSCAD reported a problem (exit code {result.returncode}):\n\n"
        f"{detail or '(no output)'}"
    )


# -- tools: workspace / file management ----------------------------------


@mcp.tool()
def list_models() -> str:
    """List all OpenSCAD (.scad) model files in the workspace."""
    files = sandbox.list_scad()
    if not files:
        return f"Workspace is empty: {sandbox.root}\n(No .scad files yet.)"
    listing = "\n".join(f"  - {f}" for f in files)
    return f"Workspace: {sandbox.root}\n\n{len(files)} model(s):\n{listing}"


@mcp.tool()
def read_model(path: str) -> str:
    """Read the source of a .scad model file, relative to the workspace root."""
    try:
        return sandbox.read(path)
    except SandboxError as exc:
        return f"Error: {exc}"


@mcp.tool()
def write_model(path: str, content: str, validate: bool = True) -> str:
    """Create or overwrite a .scad model file with *content*.

    When *validate* is true (default) the model is compile-checked afterward
    and any OpenSCAD errors/warnings are returned so you can iterate.
    """
    try:
        saved = sandbox.write(path, content)
    except SandboxError as exc:
        return f"Error: {exc}"
    rel = sandbox.relative(saved)
    if not validate:
        return f"Wrote {rel} ({len(content)} bytes)."
    try:
        result = _openscad().validate(saved)
    except OpenSCADError as exc:
        return f"Wrote {rel}, but validation could not run: {exc}"
    return _summarize(result, f"Wrote {rel} ({len(content)} bytes). Validation passed.")


@mcp.tool()
def edit_model(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    validate: bool = True,
) -> str:
    """Edit an existing .scad file by replacing *old_string* with *new_string*.

    By default *old_string* must match exactly once (a safety check). Set
    *replace_all* to replace every occurrence. Useful for tweaking existing
    models without rewriting the whole file.
    """
    try:
        source = sandbox.read(path)
    except SandboxError as exc:
        return f"Error: {exc}"

    count = source.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {path}."
    if count > 1 and not replace_all:
        return (
            f"Error: old_string matches {count} times in {path}. "
            "Provide more context to make it unique, or set replace_all=true."
        )

    updated = source.replace(old_string, new_string) if replace_all else source.replace(
        old_string, new_string, 1
    )
    try:
        saved = sandbox.write(path, updated)
    except SandboxError as exc:
        return f"Error: {exc}"

    replaced = count if replace_all else 1
    rel = sandbox.relative(saved)
    if not validate:
        return f"Edited {rel} ({replaced} replacement(s))."
    try:
        result = _openscad().validate(saved)
    except OpenSCADError as exc:
        return f"Edited {rel}, but validation could not run: {exc}"
    return _summarize(result, f"Edited {rel} ({replaced} replacement(s)). Validation passed.")


@mcp.tool()
def import_model(source_path: str, dest_path: str | None = None) -> str:
    """Import an external .scad file from disk into the workspace.

    *source_path* is an absolute or relative path on the host filesystem. The
    file is copied to *dest_path* inside the workspace (defaults to the source
    file name at the workspace root).
    """
    src = Path(source_path).expanduser()
    if not src.is_file():
        return f"Error: source file not found: {source_path}"
    if src.suffix.lower() != ".scad":
        return f"Error: only .scad files can be imported, got {src.suffix!r}."
    target = dest_path or src.name
    try:
        dest = sandbox.require_scad(target)
    except SandboxError as exc:
        return f"Error: {exc}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    except OSError as exc:
        return f"Error copying file: {exc}"
    return f"Imported {src} -> {sandbox.relative(dest)}"


# -- tools: validate / render / export -----------------------------------


@mcp.tool()
def validate_model(path: str, defines: dict[str, Any] | None = None) -> str:
    """Compile-check a .scad model and return any errors or warnings.

    *defines* optionally overrides model variables (name -> value) for the
    check, mirroring OpenSCAD's ``-D`` flag.
    """
    try:
        scad = sandbox.require_scad(path)
    except SandboxError as exc:
        return f"Error: {exc}"
    if not scad.is_file():
        return f"Error: file not found: {path}"
    try:
        result = _openscad().validate(scad, defines=defines)
    except OpenSCADError as exc:
        return f"Error: {exc}"
    return _summarize(result, "Validation passed: no errors reported.")


@mcp.tool(structured_output=False)
def render_preview(
    path: str,
    view: str | None = None,
    img_width: int = 1024,
    img_height: int = 768,
    camera: str | None = None,
    auto_fit: bool = True,
    show_axes: bool = False,
    color_scheme: str | None = None,
    full_render: bool = False,
    open_in_viewer: bool = False,
    defines: dict[str, Any] | None = None,
) -> list[Any]:
    """Render one PNG of a model and return it, plus the saved file path.

    The PNG is always written to ``renders/<model>__<view>.png`` in the
    workspace so you can read it directly if the inline image is unavailable.

    Camera:
      * *view* — a named preset: front, back, left, right, top, bottom, iso,
        iso_back. Auto-framed, so you never guess a distance.
      * *camera* — a raw OpenSCAD string (``tx,ty,tz,rx,ry,rz,dist`` or
        ``eye_x,y,z,center_x,y,z``); pass auto_fit=false to honor its distance.
      * neither — OpenSCAD's default diagonal view.

    Set *show_axes* to overlay axes + scale markers (helpful for orientation).
    Set *full_render* for accurate CSG geometry (slower). To see several angles
    at once, prefer ``render_views`` which returns a single contact sheet.
    """
    try:
        scad = sandbox.require_scad(path)
    except SandboxError as exc:
        return [f"Error: {exc}"]
    if not scad.is_file():
        return [f"Error: file not found: {path}"]

    tag = (view or "view").lower()
    out_png = sandbox.resolve(f"{RENDER_DIR}/{scad.stem}__{tag}.png")
    try:
        result = _openscad().render_preview(
            scad,
            out_png,
            img_width=img_width,
            img_height=img_height,
            view=view,
            camera=camera,
            auto_fit=auto_fit,
            show_axes=show_axes,
            color_scheme=color_scheme,
            full_render=full_render,
            defines=defines,
        )
    except OpenSCADError as exc:
        return [f"Error: {exc}"]

    if not result.ok or not out_png.is_file():
        return [
            f"Render failed (exit code {result.returncode}):\n\n"
            f"{filter_noise(result.messages) or '(no output)'}"
        ]

    rel = sandbox.relative(out_png)
    status = f"PNG saved to {rel} (view={view or 'default'}). Read that file if the image is not shown inline."
    warnings = filter_noise(result.messages)
    if warnings:
        status += f"\n\n{warnings}"
    _maybe_open(out_png, force=open_in_viewer)
    return [Image(path=str(out_png)), status]


@mcp.tool(structured_output=False)
def render_views(
    path: str,
    views: list[str] | None = None,
    tile_width: int = 512,
    tile_height: int = 512,
    show_axes: bool = False,
    full_render: bool = False,
    open_in_viewer: bool = False,
    defines: dict[str, Any] | None = None,
) -> list[Any]:
    """Render several views of a model and return ONE tiled contact-sheet image.

    This is the efficient way to inspect 3D shape: instead of many separate
    renders, you get a single labeled grid (front/right/top/iso by default) in
    one call. *views* may be any subset of the presets: front, back, left,
    right, top, bottom, iso, iso_back. The sheet is saved to
    ``renders/<model>__contact.png``.
    """
    try:
        scad = sandbox.require_scad(path)
    except SandboxError as exc:
        return [f"Error: {exc}"]
    if not scad.is_file():
        return [f"Error: file not found: {path}"]

    chosen = [v.lower() for v in (views or list(DEFAULT_VIEWS))]
    unknown = [v for v in chosen if v not in VIEW_PRESETS]
    if unknown:
        allowed = ", ".join(sorted(VIEW_PRESETS))
        return [f"Error: unknown view(s) {unknown}. Available: {allowed}."]

    out_dir = sandbox.resolve(RENDER_DIR)
    try:
        rendered, last = _openscad().render_views(
            scad,
            Path(out_dir),
            views=chosen,
            tile_width=tile_width,
            tile_height=tile_height,
            show_axes=show_axes,
            full_render=full_render,
            defines=defines,
        )
    except OpenSCADError as exc:
        return [f"Error: {exc}"]

    if not rendered:
        return [
            f"All view renders failed (exit code {last.returncode}):\n\n"
            f"{filter_noise(last.messages) or '(no output)'}"
        ]

    sheet_path = sandbox.resolve(f"{RENDER_DIR}/{scad.stem}__contact.png")
    try:
        tile_images(rendered, Path(sheet_path))
    except Exception as exc:  # Pillow/IO errors
        return [f"Error building contact sheet: {exc}"]

    rel = sandbox.relative(sheet_path)
    names = ", ".join(name for name, _ in rendered)
    status = (
        f"Contact sheet saved to {rel} (views: {names}). "
        "Read that file if the image is not shown inline."
    )
    warnings = filter_noise(last.messages)
    if warnings:
        status += f"\n\n{warnings}"
    _maybe_open(sheet_path, force=open_in_viewer)
    return [Image(path=str(sheet_path)), status]


@mcp.tool()
def measure_model(path: str, defines: dict[str, Any] | None = None) -> str:
    """Report ground-truth dimensions of a model (bounding box + triangle count).

    Exports geometry internally and measures it, so you get real numbers — the
    overall X/Y/Z size, min/max corners, and triangle count — instead of
    estimating from a render. *defines* applies parametric overrides first.
    """
    try:
        scad = sandbox.require_scad(path)
    except SandboxError as exc:
        return f"Error: {exc}"
    if not scad.is_file():
        return f"Error: file not found: {path}"
    try:
        stats = _openscad().measure(scad, defines=defines)
    except OpenSCADError as exc:
        return f"Error: {exc}"

    if not stats.get("ok"):
        err = stats.get("error", "no geometry produced (empty or non-manifold model)")
        return f"Could not measure {path}: {err}"

    sx, sy, sz = stats["size"]
    lines = [
        f"Measurements for {sandbox.relative(scad)}:",
        f"  size  (x,y,z): {sx} x {sy} x {sz}",
        f"  min corner:    {stats['min']}",
        f"  max corner:    {stats['max']}",
        f"  triangles:     {stats['triangles']}",
    ]
    if stats.get("warnings"):
        lines.append(f"\n{stats['warnings']}")
    return "\n".join(lines)


@mcp.tool()
def export_model(
    path: str,
    out_path: str,
    export_format: str | None = None,
    defines: dict[str, Any] | None = None,
) -> str:
    """Export a model to a 3D/2D file (STL, 3MF, OFF, AMF, SVG, DXF, ...).

    The format is inferred from *out_path*'s extension. *export_format* can
    force a variant (e.g. ``binstl`` or ``asciistl``). *defines* overrides
    model variables for a parametric export.
    """
    try:
        scad = sandbox.require_scad(path)
        target = sandbox.validate_export_target(out_path)
    except SandboxError as exc:
        return f"Error: {exc}"
    if not scad.is_file():
        return f"Error: file not found: {path}"
    try:
        result = _openscad().export(
            scad, target, defines=defines, export_format=export_format
        )
    except OpenSCADError as exc:
        return f"Error: {exc}"
    if result.ok and target.is_file():
        size = target.stat().st_size
        return _summarize(
            result, f"Exported {sandbox.relative(scad)} -> {sandbox.relative(target)} ({size} bytes)."
        )
    return _summarize(result, "Export reported success but no file was produced.")


@mcp.tool()
def workspace_info() -> str:
    """Report the workspace path, OpenSCAD version, and supported formats."""
    try:
        version = _openscad().version()
    except OpenSCADError as exc:
        version = f"(unavailable: {exc})"
    from .sandbox import EXPORT_SUFFIXES

    formats = ", ".join(sorted(s.lstrip(".") for s in EXPORT_SUFFIXES))
    return (
        f"Workspace: {sandbox.root}\n"
        f"OpenSCAD:  {version}\n"
        f"Export formats: {formats}"
    )

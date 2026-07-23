"""Thin wrapper around the ``openscad`` command-line interface.

Handles locating the binary, running renders/exports/validation, translating
``-D`` parameter overrides, and normalizing OpenSCAD's stdout/stderr into
structured results.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Named camera orientations as OpenSCAD gimbal rotations (rot_x, rot_y, rot_z).
# Combined with --viewall the distance is auto-fit, so these frame any model.
# Verified empirically against an asymmetric test model.
VIEW_PRESETS: dict[str, tuple[float, float, float]] = {
    "front": (90, 0, 0),
    "back": (90, 0, 180),
    "left": (90, 0, 90),
    "right": (90, 0, 270),
    "top": (0, 0, 0),
    "bottom": (180, 0, 0),
    "iso": (55, 0, 25),
    "iso_back": (55, 0, 205),
}

# The default set of views for a contact sheet.
DEFAULT_VIEWS = ("front", "right", "top", "iso")

# Lines OpenSCAD prints that are noise for an LLM (cache stats, timings, etc.).
_NOISE_PREFIXES = (
    "CGAL",
    "Total rendering time",
    "Top level object is",
    "Compiling design",
    "Rendering Polygon Mesh",
    "Geometries in cache",
    "Geometry cache size",
    "PolySets in cache",
    "CGAL Polyhedrons in cache",
    "Cache size",
    "rendering time",
    "Normalized CSG tree has",
    "Compiling design",
    "Rendering finished",
    "Simple:",
)

# Manifold-related warnings ARE useful — do not silence them.
_KEEP_SUBSTRINGS = ("WARNING", "ERROR", "manifold", "not a valid")


def filter_noise(text: str) -> str:
    """Drop OpenSCAD's cache/timing chatter while keeping warnings and errors."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(k.lower() in stripped.lower() for k in _KEEP_SUBSTRINGS):
            kept.append(stripped)
            continue
        if any(stripped.startswith(p) for p in _NOISE_PREFIXES):
            continue
        kept.append(stripped)
    return "\n".join(kept)


class OpenSCADError(Exception):
    """Raised when the OpenSCAD binary is missing or a run fails to launch."""


@dataclass
class RunResult:
    """Outcome of an OpenSCAD invocation."""

    ok: bool
    returncode: int
    stdout: str
    stderr: str
    command: list[str] = field(default_factory=list)

    @property
    def messages(self) -> str:
        """Combined, trimmed compiler output (warnings + errors)."""
        return "\n".join(p for p in (self.stdout.strip(), self.stderr.strip()) if p)


def _format_defines(defines: dict[str, Any] | None) -> list[str]:
    """Translate a mapping of variable overrides into ``-D name=value`` args.

    String values are quoted so OpenSCAD receives them as strings; numbers and
    booleans are passed through as literals. Callers may also pass a raw string
    value that already contains OpenSCAD syntax (e.g. ``"[1,2,3]"``) by wrapping
    it themselves.
    """
    if not defines:
        return []
    args: list[str] = []
    for name, value in defines.items():
        if isinstance(value, bool):
            literal = "true" if value else "false"
        elif isinstance(value, (int, float)):
            literal = repr(value)
        else:
            # Escape embedded quotes and wrap as an OpenSCAD string literal.
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            literal = f'"{escaped}"'
        args.extend(["-D", f"{name}={literal}"])
    return args


def camera_for_view(view: str) -> str:
    """Build an OpenSCAD camera string for a named *view* preset.

    Uses the translate/rotate/distance form with zero translation and zero
    distance; callers pair this with ``--viewall`` so the distance auto-fits.
    """
    key = view.strip().lower()
    if key not in VIEW_PRESETS:
        allowed = ", ".join(sorted(VIEW_PRESETS))
        raise OpenSCADError(f"Unknown view {view!r}. Available: {allowed}.")
    rx, ry, rz = VIEW_PRESETS[key]
    return f"0,0,0,{rx},{ry},{rz},0"


class OpenSCAD:
    """Locate and drive the OpenSCAD CLI."""

    def __init__(self, binary: str | None = None, backend: str = "Manifold") -> None:
        resolved = binary or shutil.which("openscad")
        if not resolved:
            raise OpenSCADError(
                "Could not find the 'openscad' binary on PATH. Install OpenSCAD "
                "(e.g. `brew install openscad`) or set OPENSCAD_MCP_BINARY."
            )
        self.binary = resolved
        self.backend = backend

    # -- low-level -------------------------------------------------------

    def _run(self, args: list[str], timeout: int) -> RunResult:
        command = [self.binary, *args]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenSCADError(
                f"OpenSCAD timed out after {timeout}s. The model may be too "
                "complex, or $fn/resolution too high."
            ) from exc
        except OSError as exc:
            raise OpenSCADError(f"Failed to launch OpenSCAD: {exc}") from exc
        return RunResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            command=command,
        )

    def version(self) -> str:
        result = self._run(["--version"], timeout=15)
        return result.messages or "unknown"

    # -- high-level operations ------------------------------------------

    def validate(
        self,
        scad_path: Path,
        defines: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> RunResult:
        """Compile the model to a CSG tree to surface syntax/semantic errors.

        Exporting to ``.csg`` evaluates the script without doing full geometry
        tessellation, so it is fast and still catches undefined variables,
        syntax errors, and most warnings.
        """
        out = scad_path.with_suffix(".validate.csg")
        args = [
            "--backend", self.backend,
            "-o", str(out),
            *_format_defines(defines),
            str(scad_path),
        ]
        try:
            return self._run(args, timeout=timeout)
        finally:
            out.unlink(missing_ok=True)

    def render_preview(
        self,
        scad_path: Path,
        out_png: Path,
        *,
        img_width: int = 1024,
        img_height: int = 768,
        view: str | None = None,
        camera: str | None = None,
        auto_fit: bool = True,
        color_scheme: str | None = None,
        show_axes: bool = False,
        full_render: bool = False,
        defines: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> RunResult:
        """Render a PNG preview of the model.

        Camera selection, in priority order:
          * *view* — a named preset (see VIEW_PRESETS: front/right/top/iso/...).
          * *camera* — a raw OpenSCAD camera string.
          * neither — OpenSCAD's default auto-framed diagonal view.

        When *auto_fit* is true (default) ``--viewall`` frames the object, so
        preset views need no distance guess. Set *show_axes* to overlay axes
        and scale markers. Set *full_render* for accurate CSG geometry (slower).
        """
        out_png.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "--backend", self.backend,
            "-o", str(out_png),
            "--imgsize", f"{img_width},{img_height}",
        ]
        if full_render:
            args += ["--render", ""]
        else:
            args += ["--preview", "throwntogether"]

        cam = camera
        if view is not None:
            cam = camera_for_view(view)

        args += ["--autocenter"]
        if auto_fit:
            args += ["--viewall"]
        if cam:
            args += ["--camera", cam]
        if show_axes:
            args += ["--view", "axes,scales"]
        if color_scheme:
            args += ["--colorscheme", color_scheme]
        args += _format_defines(defines)
        args.append(str(scad_path))
        return self._run(args, timeout=timeout)

    def render_views(
        self,
        scad_path: Path,
        out_dir: Path,
        *,
        views: list[str] | tuple[str, ...] = DEFAULT_VIEWS,
        tile_width: int = 512,
        tile_height: int = 512,
        show_axes: bool = False,
        full_render: bool = False,
        defines: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> tuple[list[tuple[str, Path]], RunResult]:
        """Render each named view to its own PNG under *out_dir*.

        Returns a list of (view_name, png_path) for successful renders plus the
        last RunResult (carrying any warnings/errors). Tiling into a single
        contact-sheet image is done by the caller (see imaging.tile_images).
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[tuple[str, Path]] = []
        last = RunResult(ok=True, returncode=0, stdout="", stderr="")
        for name in views:
            png = out_dir / f"{scad_path.stem}__{name}.png"
            last = self.render_preview(
                scad_path,
                png,
                img_width=tile_width,
                img_height=tile_height,
                view=name,
                auto_fit=True,
                show_axes=show_axes,
                full_render=full_render,
                defines=defines,
                timeout=timeout,
            )
            if last.ok and png.is_file():
                rendered.append((name, png))
        return rendered, last

    def export(
        self,
        scad_path: Path,
        out_path: Path,
        *,
        defines: dict[str, Any] | None = None,
        export_format: str | None = None,
        timeout: int = 300,
    ) -> RunResult:
        """Export the model to a 3D/2D format inferred from *out_path*'s suffix.

        *export_format* can override the format (e.g. ``binstl`` vs ``asciistl``).
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["--backend", self.backend, "-o", str(out_path)]
        if export_format:
            args += ["--export-format", export_format]
        args += _format_defines(defines)
        args.append(str(scad_path))
        return self._run(args, timeout=timeout)

    def measure(
        self,
        scad_path: Path,
        *,
        defines: dict[str, Any] | None = None,
        timeout: int = 300,
    ) -> dict[str, Any]:
        """Compute ground-truth geometry stats by exporting a temporary STL.

        Returns a dict with the axis-aligned bounding box (min/max/size per
        axis), triangle count, and whether geometry was produced. Gives the
        model real dimensions instead of eyeballing a render.
        """
        with tempfile.TemporaryDirectory() as tmp:
            stl = Path(tmp) / f"{scad_path.stem}.stl"
            result = self.export(
                scad_path, stl, defines=defines,
                export_format="binstl", timeout=timeout,
            )
            if not result.ok or not stl.is_file():
                return {
                    "ok": False,
                    "error": filter_noise(result.messages) or "export failed",
                }
            stats = _stl_bounds(stl)
        stats["ok"] = stats.get("triangles", 0) > 0
        warnings = filter_noise(result.messages)
        if warnings:
            stats["warnings"] = warnings
        return stats


def _stl_bounds(path: Path) -> dict[str, Any]:
    """Parse a binary STL and return bounding box + triangle count."""
    data = path.read_bytes()
    if len(data) < 84:
        return {"triangles": 0}
    (tri_count,) = struct.unpack_from("<I", data, 80)
    if len(data) < 84 + tri_count * 50:
        # Not a well-formed binary STL (could be ASCII); bail gracefully.
        return {"triangles": 0}
    inf = float("inf")
    lo = [inf, inf, inf]
    hi = [-inf, -inf, -inf]
    offset = 84
    for _ in range(tri_count):
        # skip 12-byte normal, read 3 vertices (9 floats)
        verts = struct.unpack_from("<9f", data, offset + 12)
        for v in range(3):
            for axis in range(3):
                val = verts[v * 3 + axis]
                if val < lo[axis]:
                    lo[axis] = val
                if val > hi[axis]:
                    hi[axis] = val
        offset += 50
    if tri_count == 0:
        return {"triangles": 0}
    size = [round(hi[i] - lo[i], 4) for i in range(3)]
    return {
        "triangles": tri_count,
        "min": [round(v, 4) for v in lo],
        "max": [round(v, 4) for v in hi],
        "size": size,  # [x, y, z] overall dimensions
    }

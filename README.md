# openscad-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an LLM **create, edit, render, and export [OpenSCAD](https://openscad.org) models** — with a PNG preview feedback loop so the model can *see* what it built and iterate.

All file operations are confined to a sandboxed workspace directory.

## Features

- **Author models** — write and validate `.scad` source
- **Edit existing models** — targeted string replacements on files already in the workspace, or import external `.scad` files
- **Preview feedback loop** — `render_preview` returns a PNG the model can inspect, so it can fix geometry and re-render
- **Multi-view contact sheet** — `render_views` renders front/right/top/iso (or any presets) into one labeled image in a single call
- **Ground-truth measurement** — `measure_model` reports the real bounding box and triangle count so the model reasons about dimensions instead of guessing
- **Export** — STL, 3MF, OFF, AMF, CSG, DXF, SVG, PDF, WRL
- **Parametric overrides** — pass `-D` variable values to any render/validate/export
- **Sandboxed** — no path traversal, no absolute paths, everything stays in the workspace

## Requirements

- Python 3.10+
- [OpenSCAD](https://openscad.org/downloads.html) on your `PATH` (`brew install openscad`), or set `OPENSCAD_MCP_BINARY`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

| Env var | Default | Description |
|---|---|---|
| `OPENSCAD_MCP_WORKSPACE` | `./workspace` | Directory holding all `.scad` files and renders |
| `OPENSCAD_MCP_BINARY` | (found on `PATH`) | Explicit path to the `openscad` binary |
| `OPENSCAD_MCP_AUTO_OPEN` | (off) | Set to `1`/`true` to open every render in the OS image viewer. Useful with hosts (e.g. Claude Desktop) that deliver tool images to the model but don't show them to the user. Per-call override: `open_in_viewer: true`. |

## Running

```bash
openscad-mcp          # console script
# or
python -m openscad_mcp
```

The server speaks MCP over stdio.

### MCP client config

Add to your MCP client (e.g. Claude Desktop `claude_desktop_config.json`, or Kiro's `mcp.json`):

```json
{
  "mcpServers": {
    "openscad": {
      "command": "/absolute/path/to/openscad-mcp-main/.venv/bin/openscad-mcp",
      "env": {
        "OPENSCAD_MCP_WORKSPACE": "/absolute/path/to/your/workspace"
      }
    }
  }
}
```

**If your install path contains a space** (e.g. `~/Library/Application Support/...`),
the `.venv/bin/openscad-mcp` console script will fail — a space breaks the
script's shebang line. Launch the module through the interpreter instead, which
takes the path as a plain argument and is unaffected:

```json
{
  "mcpServers": {
    "openscad": {
      "command": "/absolute/path/to/openscad-mcp-main/.venv/bin/python",
      "args": ["-m", "openscad_mcp"],
      "env": {
        "OPENSCAD_MCP_WORKSPACE": "/absolute/path/to/your/workspace"
      }
    }
  }
}
```

> **macOS note:** apps launched from Finder (like Claude Desktop) can't read
> `~/Documents`, `~/Desktop`, or `~/Downloads` without a Full Disk Access grant.
> If the server fails to start with `Operation not permitted` on `pyvenv.cfg`,
> either grant the app access under System Settings → Privacy & Security, or
> keep the project outside those protected folders.

## Tools

| Tool | Purpose |
|---|---|
| `workspace_info` | Report workspace path, OpenSCAD version, supported formats |
| `list_models` | List all `.scad` files in the workspace |
| `read_model` | Read a model's source |
| `write_model` | Create/overwrite a model (validates by default) |
| `edit_model` | Replace `old_string` with `new_string` in a model (validates by default) |
| `import_model` | Copy an external `.scad` file into the workspace |
| `validate_model` | Compile-check a model, return errors/warnings |
| `render_preview` | Render one PNG (named view preset or custom camera) and return it |
| `render_views` | Render several angles into a single labeled contact-sheet PNG |
| `measure_model` | Report ground-truth dimensions (bounding box + triangle count) |
| `export_model` | Export to STL/3MF/OFF/AMF/SVG/DXF/etc. |

### The feedback loop

The intended workflow for an LLM:

1. `write_model` — author the `.scad` source (auto-validates)
2. `render_views` — get a **single contact sheet** (front/right/top/iso) and **look at it**
3. `measure_model` — check real dimensions instead of eyeballing the render
4. `edit_model` — fix whatever looks wrong
5. repeat 2–4 until correct
6. `export_model` — produce the final STL/3MF

> **Reading renders:** every render tool writes its PNG under `renders/` and
> returns the path in its text output. If your MCP host doesn't display the
> inline image, read that file path directly — the tool tells you where it is.

### View presets

`render_preview` (`view=`) and `render_views` (`views=[...]`) accept named,
auto-framed camera presets — no need to compute camera distances:

`front`, `back`, `left`, `right`, `top`, `bottom`, `iso`, `iso_back`

`render_views` defaults to `["front", "right", "top", "iso"]`. Pass
`show_axes: true` to overlay axes and scale markers.

### Parametric overrides (`defines`)

`validate_model`, `render_preview`, `render_views`, `measure_model`, and
`export_model` accept a `defines` object mapping variable names to values,
equivalent to OpenSCAD's `-D`:

```json
{ "path": "box.scad", "defines": { "width": 40, "rounded": true } }
```

Booleans become `true`/`false`, numbers pass through as literals, and strings are quoted as OpenSCAD string literals.

### Camera (render_preview)

Omit `camera` to auto-frame the object (`--viewall --autocenter`). Otherwise use OpenSCAD's syntax:

- `translate_x,y,z,rot_x,y,z,dist`
- `eye_x,y,z,center_x,y,z`

Set `full_render: true` for accurate CSG geometry (slower) instead of the fast ThrownTogether preview.

## Example model

```openscad
// box.scad — a rounded box with a bore
width = 30;
rounded = true;

difference() {
    if (rounded)
        minkowski() { cube([width, width, 10], center=true); sphere(2, $fn=32); }
    else
        cube([width, width, 10], center=true);
    cylinder(h=30, r=6, center=true, $fn=64);
}
```

## License

MIT

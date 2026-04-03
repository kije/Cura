# Toolpath Generation Report: STL to Preview Stage Visualization

## Context

This report documents the complete data flow in Cura from loading an STL file to displaying
toolpaths in the Preview stage. It traces every major step, class, and file involved.

---

## High-Level Flowchart

```
STL File on Disk
    |
    v
+----------------------------------+
|  1. FILE LOADING                 |  CuraApplication.readLocalFile()
|     TrimeshReader parses STL     |  plugins/TrimeshReader/TrimeshReader.py
|     -> MeshData (vertices,       |
|       indices, normals)          |
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  2. SCENE NODE CREATION          |  CuraApplication._readMeshFinished()
|     CuraSceneNode created        |  cura/Scene/CuraSceneNode.py
|     Decorators attached:         |
|     - SliceableObjectDecorator   |
|     - ConvexHullDecorator        |
|     - BuildPlateDecorator        |
|     Object positioned & arranged |
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  3. SLICE TRIGGER                |  CuraEngineBackend.slice()
|     Auto-slice timer (500ms)     |  plugins/CuraEngineBackend/CuraEngineBackend.py
|     or manual "Slice" button     |
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  4. SLICE MESSAGE CONSTRUCTION   |  StartSliceJob.run()
|     Mesh vertices transformed    |  plugins/CuraEngineBackend/StartSliceJob.py
|     (world space, Y-up -> Z-up) |
|     Settings serialized          |
|     Protobuf message built       |  plugins/CuraEngineBackend/Cura.proto
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  5. ARCUS SOCKET SEND            |  Protobuf over TCP to CuraEngine
|     _socket.sendMessage(msg)     |  (external C++ process)
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  6. CURAENGINE PROCESSING        |  External C++ binary
|     (outside this codebase)      |
|     Performs: layer slicing,     |
|     wall generation, infill,     |
|     support, travel planning,    |
|     G-code generation            |
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  7. RESULT MESSAGES RECEIVED     |  CuraEngineBackend message handlers
|     - Progress (0.0->1.0)       |  _onProgressMessage()
|     - LayerOptimized (paths)     |  _onOptimizedLayerMessage()
|     - GCodeLayer (G-code text)   |  _onGCodeLayerMessage()
|     - PrintTimeMaterialEstimates |  _onPrintTimeMaterialEstimates()
|     - SlicingFinished            |  _onSlicingFinishedMessage()
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  8. LAYER DATA PROCESSING        |  ProcessSlicedLayersJob.run()
|     Protobuf -> numpy arrays     |  plugins/CuraEngineBackend/ProcessSlicedLayersJob.py
|     -> LayerPolygon objects      |  cura/LayerPolygon.py
|     -> LayerDataBuilder.build()  |  cura/LayerDataBuilder.py
|     -> LayerData (mesh + attrs)  |  cura/LayerData.py
|     -> LayerDataDecorator        |  cura/LayerDataDecorator.py
|       attached to scene node     |
+----------------+-----------------+
                 |
                 v
+----------------------------------+
|  9. PREVIEW RENDERING            |  SimulationView + SimulationPass
|     SimulationView manages UI    |  plugins/SimulationView/SimulationView.py
|     SimulationPass renders       |  plugins/SimulationView/SimulationPass.py
|     GPU shaders draw 3D tubes    |  plugins/SimulationView/layers3d.shader
|     Layer/Path sliders control   |
|     visible range                |
+----------------------------------+
```

---

## Detailed Steps

### Step 1: File Loading

**Entry point:** `CuraApplication.readLocalFile()` (`cura/CuraApplication.py:1959`)

- User opens an STL file via the UI
- Cura creates a `ReadMeshJob` which dispatches to the appropriate reader plugin
- For STL files: **TrimeshReader** (`plugins/TrimeshReader/TrimeshReader.py`) is used
  - Uses the Python `trimesh` library to parse binary/ASCII STL
  - Performs mesh repair:
    - `mesh.merge_vertices()` -- combine duplicate vertices
    - `mesh.remove_unreferenced_vertices()` -- clean orphaned verts
    - `mesh.fix_normals()` -- ensure consistent winding order
  - Converts to `MeshData` (from Uranium framework): vertices (float32 numpy), indices (int32 numpy), normals
- Other supported formats use their own readers: ThreeMFReader, AMFReader, etc.

### Step 2: Scene Node Creation

**Entry point:** `CuraApplication._readMeshFinished()` (`cura/CuraApplication.py:2034`)

- A `CuraSceneNode` is created and the `MeshData` is attached
- Decorators are added:
  - **SliceableObjectDecorator** (`cura/Scene/SliceableObjectDecorator.py`) -- marks the object as eligible for slicing
  - **ConvexHullDecorator** (`cura/Scene/ConvexHullDecorator.py`) -- computes 2D convex hull for collision detection
  - **BuildPlateDecorator** -- tracks which build plate the object belongs to
- Object is positioned on the build plate (translated so bottom sits at Y=0)
- Multiple objects are auto-arranged using **Nest2DArrange** (`cura/Arranging/Nest2DArrange.py`)

### Step 3: Slice Trigger

**Location:** `CuraEngineBackend` (`plugins/CuraEngineBackend/CuraEngineBackend.py`)

Slicing is triggered by one of:
1. **Scene change** -> `_onSceneChanged()` (line 668) -> starts 500ms debounce timer
2. **Setting change** -> `_onSettingChanged()` (line 791) -> starts debounce timer
3. **Manual "Slice" button** -> `forceSlice()` (line 330)

The debounce timer (`_change_timer`, 500ms) fires `slice()` (line 352).

### Step 4: Slice Message Construction

**Location:** `StartSliceJob.run()` (`plugins/CuraEngineBackend/StartSliceJob.py:108`)

This job runs in a background thread and:
1. Validates all print settings (checks for errors)
2. Iterates scene objects and for each:
   - Extracts mesh vertices and applies world transformation
   - Converts coordinate system: **Y-up (Cura) -> Z-up (CuraEngine)**
     ```python
     verts[:, [1, 2]] = verts[:, [2, 1]]  # Swap Y and Z
     verts[:, 1] *= -1                     # Negate new Y
     ```
   - Flattens indexed vertices into a vertex array
   - Includes UV coordinates and texture data (for multi-material painting)
3. Serializes all settings (global + per-extruder) into protobuf `Setting` messages
4. Builds the final `Slice` protobuf message

**Protobuf schema** (`plugins/CuraEngineBackend/Cura.proto`):
```protobuf
message Slice {
    repeated ObjectList object_lists = 1;  // Mesh groups
    SettingList global_settings = 2;       // All printer/print settings
    repeated Extruder extruders = 3;       // Per-extruder config
    repeated SettingExtruder limit_to_extruder = 4;
    repeated EnginePlugin engine_plugins = 5;
    ...
}
```

### Step 5: Communication with CuraEngine

- Uses **Arcus** library (protobuf over TCP/local socket)
- Socket sends the Slice message: `self._socket.sendMessage(job.getSliceMessage())`
- Backend state set to `BackendState.Processing`

### Step 6: CuraEngine Processing (External)

CuraEngine is a separate C++ process. It performs:
- Layer slicing (intersecting the mesh at Z heights)
- Wall/perimeter generation
- Infill pattern generation
- Support structure generation
- Travel path planning
- G-code generation
- Optimization of toolpath ordering

This step is outside the Cura Python codebase.

### Step 7: Receiving Results

**Location:** `CuraEngineBackend` message handlers

Messages received back from the engine:

| Message | Handler | Purpose |
|---------|---------|---------|
| `Progress` | `_onProgressMessage()` (line 833) | Progress 0.0->1.0 |
| `LayerOptimized` | `_onOptimizedLayerMessage()` (line 822) | Path segment data per layer |
| `GCodeLayer` | `_onGCodeLayerMessage()` (line 908) | G-code text lines |
| `GCodePrefix` | `_onGCodePrefixMessage()` (line 920) | G-code header |
| `PrintTimeMaterialEstimates` | `_onPrintTimeMaterialEstimates()` (line 971) | Time + material usage |
| `SlicingFinished` | `_onSlicingFinishedMessage()` (line 855) | End signal |

Each `LayerOptimized` message contains path segments with:
- `extruder` index, `line_type` (wall/infill/support/travel/etc.), `points` (coordinates),
  `line_width`, `line_thickness`, `line_feedrate`, `height`, `thickness`

G-code is accumulated in `scene.gcode_dict[build_plate]`.

### Step 8: Layer Data Processing

**Location:** `ProcessSlicedLayersJob.run()` (`plugins/CuraEngineBackend/ProcessSlicedLayersJob.py:73`)

Triggered after `SlicingFinished` is received. Steps:

1. Creates a `LayerDataBuilder` instance
2. For each stored `LayerOptimized` message:
   - Extracts bytearrays and converts to numpy arrays (line_types, points, widths, thicknesses, feedrates)
   - Converts coordinates back: Z-up -> Y-up (swaps Y/Z, negates)
   - Creates **LayerPolygon** objects (`cura/LayerPolygon.py`) -- each represents one path segment
3. `LayerDataBuilder.build()` aggregates all polygons into a single mesh with vertex attributes:
   - `vertices` -- 3D positions (Nx3)
   - `colors` -- RGBA from line type color map (Nx4)
   - `line_dimensions` -- [width, thickness] per vertex (Nx2)
   - `feedrates` -- print speed per vertex (N)
   - `extruders` -- extruder index per vertex (N)
   - `line_types` -- line type enum per vertex (N)
   - `indices` -- line segment pairs (Mx2)
4. Returns `LayerData` (subclass of `MeshData`) with element counts per layer
5. Attached to scene via `LayerDataDecorator` (`cura/LayerDataDecorator.py`)

**Line Types** (defined in `LayerPolygon`):

| Value | Type | Description |
|-------|------|-------------|
| 0 | NoneType | No type |
| 1 | Inset0Type | Outer wall |
| 2 | InsetXType | Inner walls |
| 3 | SkinType | Top/bottom surfaces |
| 4 | SupportType | Support |
| 5 | SkirtType | Skirt/brim |
| 6 | InfillType | Infill |
| 7 | SupportInfillType | Support infill |
| 8 | MoveUnretractedType | Travel (no retract) |
| 9 | MoveRetractedType | Travel (retracted) |
| 10 | SupportInterfaceType | Support interface |
| 11 | PrimeTowerType | Prime tower |

### Step 9: Preview Rendering

**SimulationView** (`plugins/SimulationView/SimulationView.py`)
- Main controller for the Preview stage
- Manages current layer number, current path number
- Provides layer/path sliders (QML: `LayerSlider.qml`, `PathSlider.qml`)
- Supports visualization modes: Material Color, Line Type, Feedrate, Thickness, Line Width, Flow Rate

**SimulationPass** (`plugins/SimulationView/SimulationPass.py`)
- Custom render pass that draws toolpaths
- Retrieves `LayerData` from scene nodes via `node.callDecoration("getLayerData")`
- Calculates which vertex ranges to render based on current layer/path selection
- Creates `RenderBatch` objects with vertex ranges
- Uses two shader variants:
  - **Modern:** `layers3d.shader` (vertex -> geometry -> fragment)
  - **Compatibility:** `layers.shader` (simpler pipeline)

**GPU Shader Pipeline** (`plugins/SimulationView/layers3d.shader`)

1. **Vertex Shader:**
   - Receives all vertex attributes (position, color, material_color, line_dim, extruder, feedrate, line_type)
   - Selects color based on visualization mode (`u_layer_view_type`):
     - 0=Material, 1=Line Type, 2=Feedrate gradient, 3=Thickness gradient, 4=Width gradient, 5=Flow gradient
   - Passes data to geometry shader

2. **Geometry Shader:**
   - Receives line segment pairs (2 vertices)
   - **Filters out** hidden elements: disabled extruders, travel moves, support, skin, infill (based on UI toggles)
   - **Travel moves** -> rendered as flat diamond-shaped lines
   - **Extrusion moves** -> rendered as 3D rectangular tubes (4 vertices per segment forming a box cross-section using line width and thickness)
   - **Start points** -> optional cube markers at path transitions

3. **Fragment Shader:**
   - Applies diffuse lighting
   - Outputs final RGBA color

**Shader uniforms controlling visibility:**
- `u_show_travel_moves`, `u_show_helpers`, `u_show_skin`, `u_show_infill`, `u_show_starts`
- `u_extruder_opacity` -- per-extruder visibility
- `u_max_feedrate` / `u_min_feedrate` -- for gradient coloring

---

## Key Files Reference

| File | Role |
|------|------|
| `cura/CuraApplication.py` | File loading entry point, mesh finish handler |
| `plugins/TrimeshReader/TrimeshReader.py` | STL/mesh file parser |
| `cura/Scene/CuraSceneNode.py` | Scene object representation |
| `cura/Scene/SliceableObjectDecorator.py` | Marks objects for slicing |
| `plugins/CuraEngineBackend/CuraEngineBackend.py` | Backend coordinator, message routing |
| `plugins/CuraEngineBackend/StartSliceJob.py` | Builds slice protobuf message |
| `plugins/CuraEngineBackend/Cura.proto` | Protobuf message definitions |
| `plugins/CuraEngineBackend/ProcessSlicedLayersJob.py` | Converts engine results to LayerData |
| `cura/LayerPolygon.py` | Path segment with line types and colors |
| `cura/LayerDataBuilder.py` | Builds mesh with vertex attributes |
| `cura/LayerData.py` | Immutable layer mesh data |
| `cura/LayerDataDecorator.py` | Attaches LayerData to scene |
| `plugins/SimulationView/SimulationView.py` | Preview stage controller |
| `plugins/SimulationView/SimulationPass.py` | Render pass for toolpaths |
| `plugins/SimulationView/layers3d.shader` | GPU shader (3D tube rendering) |

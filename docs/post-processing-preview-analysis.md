# Visualizing Post-Processing Script Effects in Cura's Toolpath Preview

## Context

**Problem:** In Cura, GCode post-processing scripts (PauseAtHeight, ChangeAtZ, Z-hop scripts, etc.) modify the GCode AFTER slicing, but the Preview/SimulationView shows the ORIGINAL unmodified toolpaths from CuraEngine. Users cannot see what their post-processing scripts actually do -- especially Z-height changes that move toolpaths outside the current layer's Z plane, or line reordering.

**Root cause:** Cura has a **dual-pipeline architecture** where preview data and GCode output flow through completely separate paths that never reconverge:

```
CuraEngine
    |
    +---> Protobuf path_segments ---> ProcessSlicedLayersJob ---> LayerData ---> SimulationView (PREVIEW)
    |
    +---> GCode text messages ------> scene.gcode_dict -------> PostProcessingPlugin.execute() ---> GCodeWriter (FILE)
```

Post-processing modifies `scene.gcode_dict` on the `writeStarted` signal (save/print button), but the preview's `LayerData` is built from protobuf messages and never updated.

**Goal:** Document viable approaches to make the preview reflect what post-processing scripts actually do to the toolpaths.

---

## Current Architecture (Key Files)

| Component | File | Role |
|-----------|------|------|
| Layer processing | `plugins/CuraEngineBackend/ProcessSlicedLayersJob.py` | Converts protobuf → LayerPolygon → LayerData (preview mesh) |
| Post-processing | `plugins/PostProcessingPlugin/PostProcessingPlugin.py:51,71-104` | Runs scripts on `writeStarted` signal; modifies `scene.gcode_dict` |
| Script base | `plugins/PostProcessingPlugin/Script.py:185-190` | `execute(data: List[str]) -> List[str]` interface |
| GCode parser | `plugins/GCodeReader/FlavorParser.py:331-532` | Parses GCode text → LayerData (used when loading .gcode files) |
| Preview view | `plugins/SimulationView/SimulationView.py` | Layer/path slider control, color schemes, visibility |
| Render pass | `plugins/SimulationView/SimulationPass.py:54-265` | OpenGL rendering with layers3d.shader |
| Layer polygon | `cura/LayerPolygon.py` | Per-polygon data: 3D points, line types, widths, thicknesses, feedrates |
| Layer data | `cura/LayerDataBuilder.py:46-122` | Builds flattened GPU vertex arrays from Layer/LayerPolygon objects |
| Layer data (immutable) | `cura/LayerData.py` | Immutable MeshData subclass holding final layer mesh |
| Decorator | `cura/LayerDataDecorator.py` | Attaches LayerData to CuraSceneNode for SimulationView to find |
| Slicing finish | `plugins/CuraEngineBackend/CuraEngineBackend.py:855-906` | `_onSlicingFinishedMessage()` triggers preview building |

### Key timing facts:
- `_onSlicingFinishedMessage()` calls `_startProcessSlicedLayersJob()` (line 896) immediately after slicing
- `PostProcessingPlugin.execute()` runs only on `writeStarted` (line 51) -- when saving/printing
- Post-processing is guarded by `;POSTPROCESSED` to prevent double execution
- `_propertyChanged()` (line 389) triggers re-slice when scripts are added/removed/configured

### Key data facts:
- CuraEngine GCode includes `;TYPE:WALL-OUTER`, `;TYPE:FILL`, `;LAYER:N` comments
- FlavorParser uses these comments to reconstruct line types and layer boundaries
- Post-processing scripts generally preserve these comments (they use them for navigation)
- FlavorParser guesses line widths from extrusion amounts (less accurate than engine protobuf data)

---

## Viable Approaches

### Approach A: Re-parse Post-Processed GCode via FlavorParser

**Concept:** After slicing, run post-processing scripts on the GCode, then use FlavorParser to parse the modified GCode back into LayerData, replacing the preview.

**Changes required:**

1. **`PostProcessingPlugin.py`** -- Add `executeForPreview()` method that runs scripts immediately after slicing (connected to backend's slicing-finished signal or a new `gcodeReady` signal), instead of / in addition to `writeStarted`. Must coordinate with existing `writeStarted` to avoid double-processing.

2. **`FlavorParser.py`** -- Refactor `processGCodeStream()` to extract a core `parseGCodeToLayerData(stream: str) -> LayerData` method that only produces LayerData without side effects (no scene node creation, no message display, no backend state changes).

3. **New `PostProcessedPreviewJob` (Job subclass)** -- Background job that:
   - Joins `gcode_dict` entries into a single string
   - Runs post-processing scripts on it
   - Calls the refactored FlavorParser to produce LayerData
   - Replaces `LayerDataDecorator` on the existing preview scene node
   - Triggers SimulationView to recalculate max layers, color limits, etc.

4. **`CuraEngineBackend.py`** -- After `_onSlicingFinishedMessage()` completes, emit a signal that triggers the PostProcessedPreviewJob (if post-processing scripts are configured).

5. **`SimulationView.py`** -- Handle LayerData replacement gracefully (recalculate layer counts, reset slider positions if layer count changed, update color scheme limits).

**What gets visualized:**
- All Z-height modifications (FlavorParser tracks Z per move in `_gCode0()`, line 182-213)
- All line reordering (parser processes lines sequentially)
- All new/removed paths
- Non-movement GCode (temperature, pauses) has no geometric representation -- invisible but harmless

**Limitations:**
- **Line width accuracy:** FlavorParser estimates widths from extrusion amounts (caps at 1.2mm, defaults to 0.35mm). Engine protobuf has precise widths. Visual quality degrades slightly.
- **Performance:** Re-parsing millions of GCode lines takes 10-30s for large prints. Must be async.
- **Layer assignment depends on `;LAYER:` comments:** If scripts don't preserve these, layer slider behavior degrades.

**Feasibility:** HIGH -- FlavorParser already works for loading .gcode files. Main work is plumbing.

---

### Approach B: Dual Layer Data with Toggle (Before/After)

**Concept:** Keep original engine-generated LayerData AND generate post-processed LayerData. User can toggle or overlay both.

**Additional changes beyond Approach A:**

1. **`LayerDataDecorator.py`** -- Add `_post_processed_layer_data` field, `getPostProcessedLayerData()`, `setPostProcessedLayerData()`.

2. **`SimulationView.py`** -- Add `showPostProcessedPreview` toggle exposed to QML. `getLayerData()` (currently at line ~287) returns either dataset based on toggle.

3. **`SimulationPass.py`** -- For overlay mode: render original data with shadow shader (semi-transparent), post-processed with normal shader. Two render batches per layer.

4. **QML UI** -- Add "Original / Post-Processed / Overlay" toggle in SimulationView menu.

**What gets visualized:**
- Same as Approach A for post-processed view
- Overlay mode makes Z-height differences dramatically visible (original at old Z, modified at new Z)
- Path order differences visible through path slider animation

**Limitations:**
- Double memory usage for layer data
- Layer count mismatch between datasets complicates slider mapping
- Overlay rendering Z-fighting between two 3D tube meshes
- More complex UI to explain

**Feasibility:** MEDIUM-HIGH -- builds on Approach A with additional rendering complexity.

---

### Approach C: Script Modification Tracking (Delta Approach)

**Concept:** Have scripts programmatically report what they change (inserted pause at layer N, modified Z from X to Y), then apply deltas to existing LayerData.

**Changes required:**

1. **`Script.py`** -- Add `getPreviewMarkers() -> List[PreviewMarker]` method with marker types: `PauseMarker(layer)`, `ZChangeMarker(layer, old_z, new_z)`, `TemperatureMarker(layer, temp)`, etc.

2. **All 20 built-in scripts** -- Each must implement `getPreviewMarkers()`.

3. **New LayerData modification logic** -- Must modify in-place or rebuild numpy vertex arrays, which is architecturally problematic since LayerData is immutable.

**Limitations (fundamental):**
- **Cannot handle arbitrary scripts:** `SearchAndReplace.py`, custom user scripts, complex conditional logic scripts cannot declaratively describe their modifications.
- **LayerData immutability:** Modifying vertex arrays in-place requires rebuilding the entire mesh anyway.
- **Inaccuracy:** Reported modifications may not match actual GCode changes.
- **Maintenance burden:** Every new script must implement the new API.

**Feasibility:** LOW for general use. MEDIUM for specific known modifications (pause markers, temperature annotations). Best combined with another approach.

---

### Approach D: Diff Visualization Pass

**Concept:** Compute geometric diff between original and post-processed LayerData; render only differences as color-coded overlay.

**Additional changes beyond Approach A + B:**

1. **New `PostProcessingDiffPass` (RenderPass subclass)** -- Renders only added/removed/modified line segments.
2. **Diff computation engine** -- Aligns layers, compares polygon point arrays.
3. **New diff shader** -- Color-coded (green=added, red=removed, yellow=Z-modified), possibly with animation.

**Limitations:**
- Most complex implementation
- Diff alignment is hard (inserted lines shift everything)
- Information overload for scripts modifying many lines
- Triple render cost

**Feasibility:** LOW-MEDIUM -- high implementation cost for marginal UX benefit over Approach B's toggle.

---

### Approach E: Early Post-Processing Timing (Recommended variant of A)

**Concept:** Move post-processing execution from `writeStarted` to immediately after slicing, so the user ONLY ever sees the post-processed preview.

**Key difference from Approach A:** Instead of showing original then replacing, the sequence is:
```
Slice -> Post-process GCode -> Parse to LayerData -> Show preview (post-processed only)
```

**Changes required:**
Same as Approach A, plus:

1. **`PostProcessingPlugin.py`** -- Disconnect from `writeStarted`. Connect to slicing-finished signal. Remove `;POSTPROCESSED` guard (execution happens exactly once per slice).

2. **Careful re-slice loop avoidance:** `_propertyChanged()` on line 389 triggers re-slice. Must not trigger during post-processing execution.

**Advantages over Approach A:**
- No "flash" of original preview then replacement
- Simpler mental model: preview always matches output

**Disadvantages:**
- Longer time before ANY preview appears (slicing + post-processing + parsing)
- No way to see original toolpaths (mitigated by Approach B's toggle)

**Feasibility:** HIGH -- same as Approach A with timing change.

---

### Approach F: Hybrid (Markers + On-Demand Full Re-parse) -- RECOMMENDED

**Concept:** Two tiers for best UX/performance balance:

**Tier 1 (immediate, lightweight):** Show visual markers on the existing preview for known post-processing effects (pause icons, Z-change indicators, temperature annotations). No GCode re-parsing needed.

**Tier 2 (on-demand, full):** "Show accurate post-processed preview" button triggers full Approach A/E re-parse. Cached after first computation.

**Changes required:**

**Tier 1:**
1. **`Script.py`** -- Add optional `getPreviewMarkers()` method (default returns empty list).
2. **Built-in scripts** -- Implement markers for their specific effects (PauseAtHeight returns `PauseMarker(layer_num)`, ChangeAtZ returns `ZChangeMarker(layer, old_z, new_z)`, etc.).
3. **`SimulationView.py` / `SimulationPass.py`** -- Render markers as 2D overlays or 3D annotations at the correct layer positions.
4. **QML UI** -- Show markers in the layer slider or as icons in the 3D view.

**Tier 2:**
5. Same as Approach A (FlavorParser refactoring, PostProcessedPreviewJob, etc.)
6. **"Show post-processed preview" button** in SimulationView UI. When clicked, triggers background re-parse. Result is cached.

**UX flow:**
1. Slicing completes -> original high-quality preview with lightweight markers (immediate)
2. User sees "Pause at layer 20" marker, "Z-change at layer 50" marker in layer slider
3. If user wants full accuracy -> clicks "Show post-processed preview" -> loading indicator -> preview replaces with GCode-parsed data
4. Toggle to switch back to original view

**Performance:** Tier 1 is instant. Tier 2 cost only when user explicitly requests.

**Feasibility:** MEDIUM-HIGH -- combines the best of C and A without their individual weaknesses.

---

## Comparative Summary

| Criterion | A: Re-parse | B: Dual Data | C: Deltas | D: Diff | E: Early | F: Hybrid |
|-----------|:-----------:|:------------:|:---------:|:-------:|:--------:|:---------:|
| Implementation complexity | Medium | High | Very High | Very High | Medium | Medium-High |
| Visualization accuracy | Good | Good | Poor | Good | Good | Good (Tier 2) |
| Z-height changes visible | Yes | Yes (overlay) | Partial | Yes | Yes | Yes (Tier 2) |
| Line reorder visible | Yes | Yes (toggle) | No | Partial | Yes | Yes (Tier 2) |
| Handles arbitrary scripts | Yes | Yes | No | Yes | Yes | Yes (Tier 2) |
| Performance overhead | High (async) | Very High | Low | Very High | High (async) | Low default |
| Time to first preview | Same | Same | Same | Same | Longer | Same |
| Line type preservation | Moderate* | Moderate* | Full | Moderate* | Moderate* | Full (Tier 1) |
| Uranium changes needed | No | Minor | No | No | No | No |
| UX clarity | Good | Complex | Limited | Complex | Simple | Very Good |

*FlavorParser preserves line types via `;TYPE:` comments (which scripts generally maintain), but loses some width/thickness precision.

## Recommended Implementation Strategy

**Phase 1: Approach A (core GCode re-parse infrastructure)**
- Refactor FlavorParser to extract `parseGCodeToLayerData()`
- Create `PostProcessedPreviewJob` background job
- Run post-processing after slicing, feed into FlavorParser, replace preview
- This alone delivers full visualization of all post-processing effects

**Phase 2: Add Approach B toggle (before/after)**
- Keep both original and post-processed LayerData
- Add toggle in SimulationView UI
- Users can compare original vs post-processed

**Phase 3: Add Tier 1 markers from Approach F**
- Lightweight markers for common scripts (pause, Z-change, temperature)
- Markers appear instantly, before full re-parse completes
- Enhanced UX while async re-parse runs in background

## Verification Plan
1. Configure a PauseAtHeight script -> verify pause location visible in preview
2. Configure ChangeAtZ script with Z offset -> verify toolpaths at modified Z height
3. Configure SearchAndReplace to reorder lines -> verify order change in path animation
4. Load a print with 500+ layers -> verify re-parse completes in reasonable time
5. Toggle between original and post-processed views -> verify both render correctly
6. Verify save/print still produces correct post-processed GCode (no double-processing)

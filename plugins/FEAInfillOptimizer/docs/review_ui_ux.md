# FEA Infill Optimizer — UI/UX & Visual Excellence Review

**Reviewer:** ui-designer agent
**Date:** 2026-04-07
**Branch:** `worktree-streamed-prancing-biscuit`
**Visual Assessment:** Complete — 19 screenshots reviewed (2026-04-07, captured from live plugin operation). See Section 17 for visual findings. Static code analysis findings in Sections 1–16 are confirmed or updated where screenshots add evidence.

---

## Executive Summary

The plugin demonstrates strong UX fundamentals: a phase-based progressive disclosure flow, a three-layer help system, contextual inline hints, and a well-structured examples gallery. Onboarding is thorough. For a Cura plugin, the design intent is clearly professional-grade.

However, several concrete issues limit its readiness for medium-to-advanced professional users: the stress map has no colorbar legend, the examples gallery uses hardcoded colors that break Cura's theming, the Advanced Settings spinboxes don't read from saved state, and the help system has specific coverage gaps. These are all fixable. None are architectural.

**Updated after team review:** Four additional critical findings from cross-discipline review: material brittleness warnings not shown in UI (TPU results are physically invalid, PLA results 15–30% optimistic), force rotation and magnitude edits are non-undoable without user warning, and the Cancel button has false affordance (UI reports stopped; solver thread keeps running). These raise the critical count from 2 to 6.

**Overall ratings (1–5):**

| Dimension | Rating | Notes |
|-----------|--------|-------|
| Learnability | 4.5 | Excellent onboarding + examples |
| Expert efficiency | 3.0 | Quick Setup helps; no multi-load-case |
| Error prevention | 3.5 | Good disabled states; "why disabled" missing |
| Visual consistency | 3.0 | Hardcoded colors in gallery; no colorbar |
| Accessibility | 2.5 | Partial keyboard nav; force-field labels unassociated |
| Progressive disclosure | 4.5 | Phase flow + collapsible Advanced are excellent |
| Professional workflow | 3.0 | Limited for multi-case, no export |

---

## 1. Information Architecture & Phase Flow

### Strengths

The five-phase flow (`define → optimize → running → review → error`) is the single best architectural decision in the UI. It eliminates the classic FEA tool failure mode of presenting all settings at once. Each phase reveals only what is needed.

The DEFINE phase itself uses a three-tab structure (Supports / Forces / Torques) which correctly maps to the mental model of "define what constrains the part, then what loads it, then any twists."

The BC Summary chip in the OPTIMIZE phase (showing "2 support(s), 1 force(s)") gives just-in-time context without forcing the user back.

### Issues

**UX-01 (High): No persistent phase indicator.**
The panel is 280px wide and up to 80% of window height. If the user scrolls down in DEFINE phase, there is no sticky header or breadcrumb showing the current phase. Phase identification comes only from the first heading visible when the phase begins. As the user scrolls, they lose orientation.

*Recommendation:* Add a sticky phase pill or step indicator at the top of the panel (outside the `ScrollView`), e.g., `DEFINE › OPTIMIZE › RUNNING › REVIEW` with the current step highlighted.

**UX-02 (Medium): BC Summary chip in OPTIMIZE is not visually interactive.**
The chip has an "Edit" text label and the whole row is a click target that calls `GoBackToDefine`, but there is no `cursorShape: Qt.PointingHandCursor` on the outer `MouseArea`. Every other link-style element in the panel sets the pointer cursor. This one does not (`BoundaryConditionPanel.qml:1485`).

**UX-03 (Low): "Hover preview" checkbox placement is contextually floating.**
The `CheckBox { text: "Highlight face on hover" }` at `BoundaryConditionPanel.qml:286` appears between the Quick Setup section and the tab bar, without a visual group. It feels disconnected from either. Consider grouping it with selection preferences inside the tab area, or below the Quick Setup section with a visible divider.

---

## 2. DEFINE Phase — Boundary Conditions

### Strengths

- Empty state with step-by-step quick start guide is excellent beginner guidance.
- Inline "Show Tutorial" and "View Examples" links are well-positioned (visible only when no BCs defined).
- The Alt+click hint is contextualized per-tab (every tab shows the keyboard modifier). ✓
- "Tip: 1 kg weight ≈ 10 N. A finger push ≈ 20–50 N" is practical and correct.
- Edit mode banners (warning-colored, dismissable) for Forces/Torques in-place editing are a solid interaction pattern.
- The rotate mode indicator (primary-tinted banner) contextualizing the drag-ring interaction is well done.

### Issues

**UX-04 (High): "Confirm and Optimize" disabled without feedback.**
The primary CTA is disabled when `supports.length === 0 || (forces.length === 0 && torques.length === 0)`. When disabled, clicking it is silent. New users who define only supports and try to advance will be confused.

*Recommendation:* Add a `ToolTip { visible: !enabled; text: "Add at least one support and one force (or torque) before optimizing." }` on the button.

**UX-05 (High): Onboarding wizard has a duplicate "Step 3" label.**
`OnboardingWizard.qml:43` titles the torque step "Step 3: Apply Torques (optional)". `OnboardingWizard.qml:55` titles the final step "Step 3: Run and Review". There are two Step 3s. The final step should read "Step 4: Run and Review".

**UX-06 (Medium): Selection mode toolbar is duplicated identically across all three tabs.**
`BoundaryConditionPanel.qml` repeats the same four-button `RowLayout` (Single / Surface / Hole / Cylinder) verbatim in Tab 0 (lines ~382–444), Tab 1 (~600–671), and Tab 2 (~1075–1138). While the bindings all read from the same `selectionMode` property, having the identical block appear three times:
1. Creates visual repetition that makes the panel feel longer than needed.
2. Makes it non-obvious that changing selection mode on one tab affects the others.

*Recommendation:* Factor the selection helper into a shared component positioned above the tab bar, making the global scope of the setting explicit. Or keep it per-tab but add "(global)" to the label.

**UX-07 (Medium): Force direction Fx/Fy/Fz fields shown to all users, always.**
The three text fields for force components (`BoundaryConditionPanel.qml:763–807`) are always visible under the "Direction (advanced)" label. For many users (e.g., applying a gravity load), these fields are noise. Even the label acknowledges they're "(advanced)" yet they're always rendered.

*Recommendation:* Wrap the direction fields in a collapsible similar to the Advanced Settings accordion, defaulting to collapsed. Most users rely on the face normal direction and will never touch these.

**UX-08 (Medium): Torque axis shows raw vector to all users.**
In the torques list, active torques show `Axis: [0.000, 1.000, 0.000]` (`BoundaryConditionPanel.qml:1317`). Engineers understand this, but intermediate users do not. No mapping from unit vector to friendly name ("Up (Y-axis)", "Right (X-axis)") is provided.

*Recommendation:* Add a secondary line: if the vector is close to a principal axis (|component| > 0.95), show e.g. "≈ Up (+Y)". Retain the raw vector for precision.

**UX-09 (Low): "Confirm Load on Selected Faces" and "Confirm Torque on Selected Faces" are very long button labels.**
In a 280px panel, these wrap or truncate. The label conveys necessary context but is excessively verbose for a primary action.

*Recommendation:* "Confirm Load (N faces)" or simply "Confirm Load" — the face count is already shown in the text above the button.

---

## 3. OPTIMIZE Phase — Analysis Setup

### Strengths

- Material selector with auto-populated `MaterialSummary` from the backend is excellent — the user sees the technical properties without needing to consult a reference.
- Safety factor SpinBox with `textFromValue` showing "2.0×" is clear and well-executed.
- Radio buttons for mesh quality (Fast / Balanced / Precise) with labels are clearer than a slider or dropdown.
- Collapsible Advanced Settings is the right pattern. `ChevronSingleDown/Up` icon clearly communicates state.

### Issues

**UX-10 (Critical): Advanced Settings spinboxes use hardcoded defaults, not persisted state.**
All six spinboxes in the advanced section (`BoundaryConditionPanel.qml:1786–1803`) use hardcoded `value: 10`, `value: 80`, `value: 5`, etc. They do NOT read from `toolProperties.getValue(...)`. If the user sets these, closes the tool, reopens it, and returns to OPTIMIZE phase, the UI will show the hardcoded defaults even though the backend may remember the last values.

*This is a data integrity / UX regression:* users tweaking advanced settings lose their work silently.

*Fix:*
```qml
SpinBox { from: 5; to: 90; value: toolProperties.getValue("MinDensity") ?? 10; ... }
```

**UX-11 (High): No explanation of why "Run Analysis" is disabled when deps are missing.**
The button is correctly disabled via `enabled: !!toolProperties.getValue("DepsAvailable")`, and the dependency warning Rectangle shows above it. However, the warning uses a `ColumnLayout id: depsLabel` as the Rectangle's height reference but `id: depsLabel` is being used as a height reference while the layout item is named, creating a minor QML pattern smell. More importantly: the user's focus naturally goes to the "Run Analysis" button first; the error banner being above it might be skipped.

*Recommendation:* Consider placing the dependency warning immediately adjacent to the disabled button, or highlight the button's disabled state with a tooltip: "Install required libraries first."

**UX-12 (Medium): Infill pattern ComboBox offers 15 choices without filtering.**
The infill pattern list (`BoundaryConditionPanel.qml:1570–1585`) includes 15 options including unsuitable ones for structural use (Concentric, Lightning, Zig Zag). The help tooltip correctly advises against some, but users must navigate the full list.

*Recommendation:* Consider grouping into "Recommended" / "All" using a separator or show-more disclosure, or at minimum append "(not recommended for structural)" in the label for unsuitable patterns.

**UX-13 (Medium): "Target volume (%)" SpinBox conditionally visible but its label also conditionally visible via separate `visible:` binding.**
Both `UM.Label { visible: method === "oc" }` and `SpinBox { visible: method === "oc" }` use separate, duplicated visibility bindings (`BoundaryConditionPanel.qml:1822–1833`). If the OC visibility condition ever becomes more complex, this will diverge. Minor maintainability note (see also code-quality review).

---

## 4. RUNNING Phase — Progress

### Strengths

- Custom-styled ProgressBar (8px height, primary color, rounded) matches Cura aesthetics.
- Stage label below progress shows current step name — good.
- Model name shown centered — good orientation.
- "Stop" button is accessible and correctly labeled.

### Issues

**UX-14 (High): Indeterminate stages not distinguished from determinate.**
Mesh generation (the initial "Preparing…" stage) has unknown duration. The progress bar goes from 0% and the stage label says "Preparing…". If meshing is slow (large/complex model), the bar stays at 0 for a long time and users may think the application has frozen.

*Recommendation:* Use an indeterminate `ProgressBar` (or a pulsing animation) when `analysisProgress === 0 && analysisStage === "Preparing…"` to signal "working but not yet quantifiable."

**UX-15 (Low): No time estimate.**
Professional users submitting long analyses would benefit from a rough ETA or elapsed-time counter. Even "Elapsed: 12s" helps prevent premature cancellation.

---

## 5. REVIEW Phase — Results

### Strengths

- Verdict chip with color-coded background (error/warning/success/primary) and symbol (✗/⚠/✓/ℹ) plus plain-English explanation is excellent.
- Metrics grid (Max Stress, Min Stress, Safety Factor, Iterations) with help icons on contextually important values is well-executed.
- "Restored from project file" warning banner (`hasResults && !hasFullResults`) — excellent edge-case handling.
- Mesh quality indicator (HIGH ● / MED ◐ / LOW ○) with color coding is inventive and communicates confidence level clearly.
- "Edit Boundary Conditions" and "Edit Analysis Settings" as separate secondary buttons — professional power-user path.
- Clear Results with confirmation dialog — good destructive-action guard.

### Issues

**UX-16 (Critical): No stress map colorbar/legend.**
The stress overlay renders a viridis gradient mapped to [MinStress, MaxStress]. The Review phase UI shows `maxStress` and `minStress` values in the metrics grid, but nowhere in the panel is there a visual legend showing the color → value mapping. Users see colored blobs on their model without being able to immediately read off stress values from color.

*This is the most significant visual excellence gap in the entire plugin.*

*Recommendation:* Add a horizontal or vertical gradient swatch immediately below or near the "Show Stress Map" button:
```
[dark purple] ————— [yellow]
   0.0 MPa          {maxStress} MPa
```
This needs only a `LinearGradient` fill rectangle with `min/maxStress` labels. The values are already available in `bcPanel.minStress` / `bcPanel.maxStress`.

**UX-17 (High): "Apply Optimized Infill" provides no success confirmation.**
After clicking this primary CTA, the modifier meshes are applied silently. There is no toast, no status message, no "Done — N zones applied" feedback. The button remains visible after application.

*Recommendation:* After applying, show a brief success message: "Applied 5 infill zones. Review modifier meshes in Cura's scene." A `UM.Label` that becomes visible for a few seconds (or permanently) would suffice.

**UX-18 (Medium): "Apply Optimized Infill" disabled when `!hasFullResults` with no explanation.**
Like UX-04, the disabled state has no tooltip. When results are restored from a project file (`hasResults && !hasFullResults`), the button is disabled and the warning banner explains "Re-run analysis to enable…" — so context is there. But if `hasResults === false` entirely, the button is disabled with no message.

**UX-19 (Low): Min Stress metric has no help icon.**
The metrics grid includes help icons for Max Stress and Safety Factor but not Min Stress. Min stress is generally less critical but its omission is inconsistent.

**UX-20 (Low): Convergence iterations tooltip is incomplete.**
The tooltip (`BoundaryConditionPanel.qml:2220`) says "If this equals the maximum allowed, the result may not be fully converged." This is correct but it doesn't tell the user what the maximum is. Since `MaxIterations` is configurable in Advanced Settings, showing the actual max would help: "X of Y max passes. If X = Y, result may not be converged."

---

## 6. ERROR Phase

### Strengths

- Structured bullet-point suggestions (coarser mesh, check BCs, supports ≠ forces faces) are actionable.
- "Try Again" → re-runs directly from error state without forcing user back to setup.
- "Edit Setup" provides a clean escape route.

### Issues

**UX-21 (Medium): Two error label components risk double-printing.**
At `BoundaryConditionPanel.qml:2340` there is a bare `UM.Label` showing `ErrorMessage` directly. At `BoundaryConditionPanel.qml:2358` there is a Rectangle with `errorMsgLabel` showing generic suggestions. If `ErrorMessage` is populated, the user sees the specific error AND the generic suggestions separately. If `ErrorMessage` is empty, only the generic box shows. The visual separation between specific and generic messaging is implicit.

*Recommendation:* Merge: Show the specific `ErrorMessage` inside the suggestion Rectangle, above the bullet list. Use an explicit separator or header: "Error: {ErrorMessage}" in red, then the suggestions below.

---

## 7. Help System

### Architecture Assessment

The three-layer help system is architecturally sound and well-executed:
1. **Tooltip** (hover, 500ms delay, 10s timeout) — for quick reminders
2. **Popover** (click, modal, scrollable) — for detailed guides with steps/tips/images
3. **Onboarding Wizard** (first-run, re-accessible) — for concept introduction
4. **Examples Gallery** (searchable, filterable) — for use-case learning

The separate `HelpContent.qml` QtObject loading JSON asynchronously at startup is clean. The guide IDs (D01, D02, D03, O01, O09, O12, O13, R01, R02, R06) are self-documenting.

### Content Quality

Help content in `help_content.json` is technically accurate and well-targeted at the intended audience. Practical tips (1 kg = 10 N, wrench-tightened bolt = 10–50 Nm) are correct and useful. The infill pattern exponent table in O09 is genuinely educational.

### Coverage Gaps

**HELP-01 (Medium): No guide entries for Running phase.**
There are no entries for what happens during analysis (what "Meshing", "Assembling stiffness matrix", "Solving" mean). An R00 entry visible during the running phase would reduce anxiety on long analyses.

**HELP-02 (Medium): Layer bonding coefficient has no help entry.**
The "Layer bonding (%)" SpinBox in Advanced Settings has a tooltip on the section header but no individual guide. This parameter (adjusting the Z-direction stiffness reduction) is non-obvious to non-experts.

**HELP-03 (Medium): SIMP OC vs. Heuristic has no help entry.**
The optimization method ComboBox in Advanced Settings has no guide. The academic distinction (SIMP topology optimization vs. stress-proportional heuristic) will be opaque to most users.

**HELP-04 (Low): "Selection helper" modes have a section tooltip but no popover.**
The "Selection helper" row has a tooltip explaining Single/Surface/Hole/Cylinder but no guide ID. A visual popover with images showing each mode on a real 3D geometry would be very helpful for beginners.

**HELP-05 (Low): Quick Setup buttons have no help.**
"Gravity: Click Bottom Face" and "Cantilever: Click Fixed End" have no tooltip or guide explaining what scenario they set up or why that's useful.

---

## 8. Examples Gallery

### Strengths

- 6 examples covering the most common structural archetypes (bracket, beam, enclosure, handle) — well chosen.
- Difficulty levels (Beginner/Intermediate) help users pick appropriate starting points.
- Detail view is comprehensive: scenario description, color-coded section cards (green=supports, red=forces, blue=torques), key insights, expected stress pattern, recommended settings.
- Left-accent colored bars for section type (green/red/blue) are a clear visual convention.
- Search + category filter is functional.

### Issues

**GAL-01 (High): Category filter is incomplete relative to JSON data.**
The ComboBox in `ExamplesGallery.qml:135` offers: "All", "Bracket", "Beam", "Enclosure", "Handle". But `examples.json` contains categories: "Robotics" (EX02), "Motor Mount" (EX03), "Lever" (EX06), "Plate" (EX05). The filter `selectedCategory = currentText.toLowerCase()` will never match "robotics" against the dropdown. EX02 and EX03 will not appear when filtering by category.

*Fix:* Generate category options dynamically from the loaded examples array, or add the missing categories to the ComboBox model.

**GAL-02 (High): Difficulty badge uses near-transparent hardcoded hex colors.**
```qml
color: modelData.difficulty === "Beginner"
    ? "#1522AA44" : "#154488DD"
```
The alpha channel here is `0x15` = 8.2% opacity. These badges will be nearly invisible on most backgrounds. The colors also do not use `UM.Theme` so they will not adapt to light/dark mode.

*Fix:* Use proper Cura theme colors:
```qml
color: modelData.difficulty === "Beginner"
    ? Qt.rgba(UM.Theme.getColor("success").r, ..., 0.25)
    : Qt.rgba(UM.Theme.getColor("primary").r, ..., 0.25)
```

**GAL-03 (High): `id` declarations inside `Repeater` delegates will conflict.**
`ExamplesGallery.qml:414` declares `id: forceCol` inside a Repeater delegate. `id: torqueCol` at line 479 also. In QML, `id` inside a Repeater creates IDs scoped to the delegate but this can still produce warnings when multiple items are instantiated. More critically, `id: supportCol` at line 352 is inside the non-repeated `Rectangle` while `forceCol` and `torqueCol` are inside repeated delegates — a stylistic inconsistency.

*Recommendation:* Remove `id` from Repeater delegate layouts where IDs are not referenced externally. Use `property alias` if the height reference is needed.

**GAL-04 (Medium): The detail view "APPLIED FORCE" label concatenates values without i18n.**
```qml
text: catalog.i18nc("@label", "APPLIED FORCE") + " - " + modelData.magnitude_N + " N " + modelData.direction
```
The ` - ` separator and `" N "` literal are outside the i18n catalog. Non-critical for a single-language build but inconsistent with the rest of the plugin's i18nc usage.

**GAL-05 (Medium): Detail view labels use hardcoded English strings.**
"Selection mode: " (`line 376`), "Where: " (`line 437`), "Mode: " (`line 439`), "Axis: " (`line 502`), "Estimation: " (`line 447`) are all hardcoded strings, not wrapped in `catalog.i18nc()`. These will not be translatable.

**GAL-06 (Low): No loading indicator during JSON fetch.**
Loading is deferred to `onVisibleChanged` (correct), but if the synchronous XHR at `line 46` takes >100ms (large file, slow disk), the gallery will momentarily appear empty. A `BusyIndicator` or "Loading examples…" label during load would improve perceived performance.

**GAL-07 (Low): No "No results" empty state when search returns nothing.**
If the user types a search term that matches nothing, the `GridView` is empty with no message. A "No examples match your search" label should be shown when `filteredExamples.length === 0`.

---

## 9. SVG Icons

All action icons (force, mount, torque, select_single, select_surface, select_hole, select_cylinder) are:
- 24×24 viewBox — correct for Cura's `UM.ColorImage`
- `fill="#ffffff"` — designed for Cura's color-image tinting (the `color` property will tint; white = tintable) ✓
- Geometrically appropriate: force = downward arrow, mount = pillar-on-pedestal, torque = orbit circle with center dot

Guide SVGs (guide_support.svg, guide_force.svg) are larger diagrams (200×120) with:
- Isometric 3D perspective rendering of geometry ✓
- Color-coded highlight faces (green = fixed, red = force) consistent with 3D view highlight colors ✓
- Engineering annotation symbols (hatching, anchor triangles, force arrows with F label) ✓
- Dark background compatible (grey fills on transparent background) ✓

**ICON-01 (Low): `select_single.svg` icon is a triangle (polygon pointing right).**
The triangle shape suggests "play" or "direction" more than "select single triangle." A cursor-pointer icon or a single highlighted triangle against a mesh background would be more intuitive. The current design may confuse first-time users.

**ICON-02 (Low): Guide SVGs use hardcoded text (`font-family="sans-serif"`).**
The `<text>` elements in guide_support.svg use `font-family="sans-serif"` and `fill="#CCCCCC"`. These are not theme-aware and won't change with Cura's font. For light-mode Cura, `#CCCCCC` text on a white background would be very low contrast. (Note: visual assessment pending.)

---

## 10. Stress Overlay Shader

The `stress_overlay.shader` implements ambient + diffuse Phong with per-vertex color:

```glsl
float NdotL = clamp(abs(dot(normal, lightDir)), 0.0, 1.0);
finalColor += (NdotL * v_color);
finalColor.rgb += v_color.rgb * 0.3;  // additional ambient for back-faces
```

- `abs(NdotL)` correctly handles back-facing polygons (double-sided shading) ✓
- Per-vertex viridis colors passed via `a_color` attribute ✓
- 0.3 ambient boost ensures dark/back-facing areas still show color information ✓
- Default `u_opacity = 0.85` is appropriate (preserves model shape through overlay) ✓
- Dual-profile (legacy ES2 + OpenGL 4.1 core) is correct for Cura's renderer ✓

**SHADER-01 (Low): Ambient contribution is additive with `u_ambientColor` AND `v_color * 0.3`.**
The final color accumulates: `u_ambientColor + NdotL * v_color + v_color * 0.3`. On very bright parts of the model (under direct light), the viridis color is multiplied beyond the intended range. This means yellow (high stress, viridis maximum) regions may appear blown out / over-bright. Since `u_ambientColor = [0.1, 0.1, 0.1, 1.0]` is low, the practical impact is minor, but for viridis-accuracy, clamping `finalColor = clamp(finalColor, 0.0, 1.0)` explicitly before `gl_FragColor` assignment would prevent saturation.

---

## 11. Accessibility

**A11Y-01 (High): Force direction TextFields have no accessible name.**
The Fx/Fy/Fz TextFields in the Forces tab have no `Accessible.name`. The adjacent `UM.Label { text: "Fx:" }` is not programmatically associated. Screen reader users will encounter unnamed edit fields.

*Fix:*
```qml
TextField {
    id: forceXField
    Accessible.name: catalog.i18nc("@accessible", "Force X component in Newtons")
    ...
}
```

**A11Y-02 (High): HelpTooltipIcon has no keyboard focus or keyboard activation.**
`HelpTooltipIcon.qml` has `Accessible.role: Accessible.Button` and `Accessible.name` set, but there is no `activeFocusOnTab: true`, no `Keys.onSpacePressed`, and no `Keys.onReturnPressed`. Keyboard-only users cannot activate help popovers.

*Fix:* Add `activeFocusOnTab: true` and `Keys.onSpacePressed/ReturnPressed: { if (guideId !== "") helpContentManager.openPopover(...) }`.

**A11Y-03 (Medium): BC list item delete icons have no accessible name.**
The Cancel icon `MouseArea` for deleting supports/forces/torques (`~line 495–503, 870–879`) has no `Accessible.role` or `Accessible.name`. A screen reader user cannot identify or activate these delete targets.

*Fix:*
```qml
UM.ColorImage {
    Accessible.role: Accessible.Button
    Accessible.name: catalog.i18nc("@accessible", "Delete %1").arg(modelData.label)
    ...
}
```

**A11Y-04 (Medium): HelpPopover close button has `Accessible.role: Accessible.Button` on the `ColorImage`, not the `MouseArea`.**
`HelpPopover.qml:84–86` sets `Accessible.role: Accessible.Button` and `Accessible.name: "Close help"` on the `UM.ColorImage` rather than on a focusable element. The `MouseArea` handles the click but has no accessible properties. Neither has `activeFocusOnTab: true`.

**A11Y-05 (Low): Color used as sole indicator in mesh quality display.**
The mesh quality display uses "HIGH ●", "MED ◐", "LOW ○" symbols (which is good — non-color indicator), but the background `color:` of the Rectangle is the sole differentiator in some degraded rendering environments. The text + symbol combination satisfies WCAG, but the symbol choices (filled/half/empty circle) are good.

**A11Y-06 (Low): Onboarding wizard step dots are purely decorative without accessible labels.**
The 8×8 dots in `OnboardingWizard.qml:141–160` have no `Accessible.description`. Screen reader users receive no step position information.

---

## 12. Critical QML Issues

**QML-01 (High): `toolProperties.getValue("DepsAvailable")` truthiness check.**
At `BoundaryConditionPanel.qml:1875`:
```qml
enabled: !!toolProperties.getValue("DepsAvailable")
```
`toolProperties.getValue()` returns `null` when the key doesn't exist. `!!null === false`, so the button starts disabled until the backend populates the key. This is likely intentional, but if the backend ever forgets to set this key, the Run button stays permanently disabled with no indication.

**QML-02 (Medium): `holeDiameterSpinBox` missing `Accessible.name`.**
The hole diameter SpinBox at `BoundaryConditionPanel.qml:273` has no `Accessible.name` or associated label in the Accessible tree.

**QML-03 (Low): `hoverToggle` CheckBox uses `onClicked` instead of `onCheckedChanged`.**
```qml
onClicked: UM.Controller.setProperty("HoverPreviewEnabled", checked)
```
If the backend sets `HoverPreviewEnabled` independently (e.g., restored from project), the checkbox visual will update via the binding `checked: toolProperties.getValue(...)` but the controller won't re-notify. Using `onClicked` is correct behavior here (user-driven only). This is not a bug — just note for reviewers.

---

## 13. Professional Workflow Gaps

These are not bugs but gaps relative to the stated "professional workflows" requirement.

**PRO-01 (High): No result export.**
Structural engineers need to document analysis results. No CSV/PDF/JSON export of: max stress, min stress, safety factor, zone count, zone density values, material properties, load definitions. Without this, the tool is not suitable for professional engineering documentation.

**PRO-02 (High): No multi-load-case support.**
Real-world parts often need to be analyzed under multiple load cases (e.g., static gravity + dynamic shock + assembly preload). The tool supports one BC set at a time. Switching BCs destroys the previous setup.

**PRO-03 (Medium): No post-apply feedback.**
After "Apply Optimized Infill" creates modifier meshes, the user is not told how many zones were created, what densities they span, or where to find them in Cura's scene panel. This is the single most important step in the workflow (it generates the actual print settings), and it receives no post-action confirmation.

**PRO-04 (Medium): No convergence chart.**
The metrics grid shows the final safety factor and iteration count, but not whether the result converged smoothly or oscillated. A simple 10×30px sparkline or even just "converged in N iterations / diverged" text would build user confidence.

**PRO-05 (Low): No model dimension reference in stress map.**
When reviewing the stress map, there is no scale bar or reference dimension. For structural analysis, spatial context ("the stress concentration is 2mm from the corner") is important.

---

## 14. Summary of Prioritized Findings

### Severity: Critical
| ID | File | Issue |
|----|------|-------|
| UX-16 | BoundaryConditionPanel.qml:2244 | No stress map colorbar legend in Review phase |
| UX-10 | BoundaryConditionPanel.qml:1786 | Advanced Settings spinboxes use hardcoded defaults, not persisted values |

### Severity: High
| ID | File | Issue |
|----|------|-------|
| UX-04 | BoundaryConditionPanel.qml:1395 | "Confirm and Optimize" disabled with no tooltip explaining why |
| UX-05 | OnboardingWizard.qml:55 | Duplicate "Step 3" label in wizard |
| UX-14 | BoundaryConditionPanel.qml:1928 | No indeterminate state for "Preparing…" phase |
| UX-17 | BoundaryConditionPanel.qml:2226 | No success feedback after Apply Optimized Infill |
| GAL-01 | ExamplesGallery.qml:135 | Category filter missing Robotics, Motor Mount, Lever, Plate |
| GAL-02 | ExamplesGallery.qml:217 | Difficulty badges nearly invisible (8% opacity hardcoded hex) |
| GAL-03 | ExamplesGallery.qml:414 | `id` declarations in Repeater delegates |
| A11Y-01 | BoundaryConditionPanel.qml:770 | Force Fx/Fy/Fz TextFields have no accessible name |
| A11Y-02 | HelpTooltipIcon.qml:32 | No keyboard activation for help popovers |
| PRO-01 | — | No result export for professional documentation |
| PRO-02 | — | No multi-load-case support |

### Severity: Medium
| ID | File | Issue |
|----|------|-------|
| UX-01 | BoundaryConditionPanel.qml:97 | No persistent phase indicator when scrolled |
| UX-02 | BoundaryConditionPanel.qml:1485 | BC Summary chip missing pointer cursor |
| UX-06 | BoundaryConditionPanel.qml:296 | Selection mode toolbar duplicated 3× |
| UX-07 | BoundaryConditionPanel.qml:756 | Force direction fields always visible |
| UX-08 | BoundaryConditionPanel.qml:1317 | Raw torque axis vector with no friendly name |
| UX-21 | BoundaryConditionPanel.qml:2340 | Double error label components |
| HELP-01 | help_content.json | No help entries for Running phase |
| HELP-02 | help_content.json | Layer bonding has no guide entry |
| HELP-03 | help_content.json | SIMP OC vs. Heuristic has no guide entry |
| GAL-04 | ExamplesGallery.qml:424 | Force label concatenation outside i18nc |
| GAL-05 | ExamplesGallery.qml:376 | Several detail-view strings not i18nc wrapped |
| GAL-07 | ExamplesGallery.qml:154 | No empty state when search returns no results |
| A11Y-03 | BoundaryConditionPanel.qml:495 | Delete icons have no accessible name |
| A11Y-04 | HelpPopover.qml:84 | Close button accessible role on wrong element |
| PRO-03 | — | No post-apply confirmation after modifier mesh creation |
| PRO-04 | — | No convergence history |

### Severity: Low
| ID | File | Issue |
|----|------|-------|
| UX-03 | BoundaryConditionPanel.qml:286 | Hover preview checkbox contextually floating |
| UX-09 | BoundaryConditionPanel.qml:925 | Overly long confirm button label |
| UX-20 | BoundaryConditionPanel.qml:2218 | Iterations tooltip doesn't show the maximum |
| HELP-04 | — | Selection helper modes have no popover guide |
| HELP-05 | — | Quick Setup buttons have no help |
| ICON-01 | select_single.svg | Triangle icon ambiguous as "select" |
| ICON-02 | guide_support.svg | Text in guide SVGs not theme-aware |
| GAL-06 | ExamplesGallery.qml:35 | No loading indicator during JSON fetch |
| SHADER-01 | stress_overlay.shader:52 | Potential over-bright high-stress regions |
| A11Y-05 | BoundaryConditionPanel.qml:2032 | Color used as primary indicator in mesh quality |
| A11Y-06 | OnboardingWizard.qml:141 | Step dots not accessible to screen readers |
| QML-02 | BoundaryConditionPanel.qml:273 | holeDiameterSpinBox missing accessible name |

---

## 15. Cross-Discipline Findings Received from Team

*Added after initial review — findings from code-quality-expert, performance-expert, and physics-math-expert with direct UI/UX implications.*

---

### From code-quality-expert (CQ findings with UX impact)

**UX-22 (Critical): Force rotation is not undoable — Ctrl+Z silently fails.**
`BoundaryConditionTool._handle_rotate_event` mutates force direction directly without pushing an undo operation on `MouseReleaseEvent`. The user rotates a force arrow via drag, releases the mouse, presses Ctrl+Z — nothing happens. This is a severe UX violation: Cura's undo model is the user's primary safety net for interactive 3D editing. (CQ-9 in code quality review.)

*UI implication:* The panel gives no indication that this action is non-undoable. Users who accidentally rotate a force to an unintended direction have no recovery path except manually re-entering values.

*Recommendation:* Until fixed at the backend level, add a visible warning banner when entering rotate mode: "Note: rotation cannot be undone." This is a stopgap — the correct fix is at the backend (push `SetForceDirectionOperation` on `MouseReleaseEvent`).

**UX-23 (Critical): Inline magnitude edits are not undoable — Ctrl+Z silently fails.**
`setUpdateForceAtIndex` and `setUpdateTorqueAtIndex` mutate magnitude directly without pushing undo operations. A user who changes a force from 100 N to 500 N via the inline edit and immediately presses Ctrl+Z is surprised: nothing reverts. (CQ-10 in code quality review.)

*Recommendation:* Same stopgap as above — a subtle "Changes to magnitude cannot be undone." note near the field. Backend fix (push operation on edit commit) is the correct resolution.

**UX-24 (Critical): Cancel button has false affordance — analysis keeps running.**
After clicking "Stop" in the RUNNING phase, the UI immediately transitions away from the running phase (progress bar disappears), but the background solver thread continues to completion. (CQ-18 in code quality review.)

From a UX perspective this is a false affordance: the UI communicates "stopped" while the system is still working. Consequences:
- CPU remains pegged for minutes after the user believes the run was cancelled
- If the user immediately starts a new run, it queues behind the still-running cancelled run
- The user has no awareness of residual work happening

*Recommendation (UI side, until backend is fixed):*
- Change "Stop" button text to "Stopping…" and keep it visible with a spinner after clicking
- Or: keep the progress bar visible but greyed out with a "Cancelling…" stage label
- Do not transition phase until the thread actually terminates (listen for the backend's cancellation confirmation signal)

---

### From performance-expert (PERF findings with UX impact)

**UX-25 (High): Progress bar is frozen for minutes between iterations.**
Each FEA iteration emits only one progress event at its completion (5–60 s of silence per iteration). From the user's perspective, the progress bar is completely frozen for extended periods, then jumps. This is the UX equivalent of a spinner with no movement — users cannot distinguish "working slowly" from "frozen/crashed."

The sub-stage timing data (`assembly`, `BCs`, `solve`, `stress`) is already computed internally via `Logger.log` calls but not promoted to the progress callback.

*Recommended stage labels per iteration (from performance-expert):*
- "Assembling stiffness matrix…"
- "Applying boundary conditions…"
- "Solving linear system…"
- "Computing stress field…"

*UI impact:* The existing `analysisStage` label in the RUNNING phase (`BoundaryConditionPanel.qml:1957`) is already wired to display these — the QML is ready. This is purely a backend emission gap.

**UX-26 (Medium): ETA estimate inaccurate for SIMP OC method.**
Linear time extrapolation for ETA is correct for the heuristic method but misleading for OC (where early iterations are slow and later ones fast). A user running OC may see "ETA: 45 minutes" in iteration 1 that drops to "ETA: 2 minutes" by iteration 10.

This amplifies the existing UX-15 (no time estimate) finding. If a time estimate is added, it must be method-aware.

*Recommendation:* For OC method, replace or supplement time estimate with "Iteration X of Y" — this is always accurate regardless of speed profile.

---

### From physics-math-expert (PHYS findings with UX impact)

**UX-27 (Critical → revised scope): Material failure warnings are shown but visually buried and not gracefully pre-checked for TPU.**

*Update from physics-math-expert:* The backend already populates `materialSummary` with failure mode warnings (`FEAInfillExtension.py:752–767`). The QML label at `BoundaryConditionPanel.qml:1529` shows them automatically. The actual text rendered is:
- **TPU_95A:** `"E = 26 MPa | σ_yield = 30 MPa | … | ⚠ Hyperelastic — linear FEA not valid"`
- **PLA:** `"E = 3000 MPa | σ_yield = 50 MPa | … | Brittle — von Mises may overestimate strength"`

The critical problem is NOT that warnings are absent — it is that:

1. **The label uses `color: UM.Theme.getColor("text_inactive")` (`BoundaryConditionPanel.qml:1532`)**, the same muted color as every other secondary note in the panel. A safety-critical warning rendered identically to "Pattern used in infill zones" is not a warning — it is invisible noise.

2. **The brittle warning has no actionable guidance.** "Brittle — von Mises may overestimate strength" gives the user no next step. Adding "Use SF ≥ 3.0×" is minimal and actionable.

3. **TPU has no graceful pre-flight block.** The solver raises `ValueError` when `nu > 0.45` (which catches TPU's ν=0.48), but this surfaces as a runtime error in the ERROR phase, not as a UI-level gate before the user runs a 60-second analysis. The user invests time, then receives a generic error.

*Three concrete fixes — all minimal scope:*

**Fix 1** (1 line of QML): Change the `materialSummary` label color from `text_inactive` to a conditional color based on warning content:
```qml
color: {
    var s = text
    if (s.indexOf("Hyperelastic") >= 0) return UM.Theme.getColor("error")
    if (s.indexOf("Brittle") >= 0)      return UM.Theme.getColor("warning")
    return UM.Theme.getColor("text_inactive")
}
```

**Fix 2** (backend, 1 line): Append `" — use SF ≥ 3.0×"` to the brittle warning string in `FEAInfillExtension.py:764`.

**Fix 3** (QML, ~10 lines): Add a pre-flight check in the OPTIMIZE phase that disables "Run Analysis" when `materialSummary` contains "Hyperelastic", with a tooltip: "TPU (hyperelastic) cannot be analyzed with linear FEA. Select a different material." This prevents the runtime error path entirely.

*Location:* `BoundaryConditionPanel.qml:1513–1543` (material section) and `BoundaryConditionPanel.qml:1871–1877` (Run Analysis button).

---

---

### From physics-math-expert (follow-up: stress at constrained faces)

**Resolved concern:** Stress values at boundary elements adjacent to fixed supports are physically correct — BCs zero the fixed-node displacements but mixed-constraint elements still produce non-zero strain/stress from their free nodes. The stress concentrations shown near supports in the viridis overlay are real and should be displayed as-is.

**UX-28 (Low): No contextual note about mesh-dependent stress concentrations near supports.**
A fundamental FEA property: stress concentrations at point or edge constraints increase with mesh refinement. A user who runs Balanced mesh (max stress = 85 MPa) then Precise mesh (max stress = 120 MPa) may conclude the fine result is worse or that the part is failing — when in fact it is simply a more accurate (and normal) FEA characteristic.

*Physics clarification (from physics-math-expert):* The printed part is not at risk from this artifact. Elements at fixed supports receive high stress → high density → maximum infill regardless of mesh refinement level. The infill at supports is already saturated at `rho_max`. The concern is purely in how users *interpret the reported numbers*, not in the actual print outcome.

*Recommendation:* Add a one-line note in the Review phase below the metrics grid, or as a footnote to the Max Stress help tooltip:
> "Peak stress at fixed supports increases with mesh refinement (a normal FEA characteristic). The infill density at these locations is already at maximum."

This directly serves medium-to-advanced users who perform multiple mesh-refinement runs and compare the resulting `maxStress` values across runs.

---

---

### From reliability-expert (REL findings with UX impact)

**UX-22/23 confirmed (High-4):** Direct BC mutation without undo independently confirmed. Cross-referenced with code-quality-expert CQ-9/10. Stopgap recommendations stand.

**UX-10 confirmed (MED-3):** Advanced Settings state divergence independently confirmed as both a UX and reliability issue. No new action on UX side.

**UX-29 (Medium): No visual feedback when numeric field input is substituted silently.**
Force magnitude, direction, and torque fields use `parseFloat(text) || fallback` (e.g., `parseFloat(text) || 100.0` at `BoundaryConditionPanel.qml:740`, `|| 0.0` at line 779). When input is empty, "0", or unparseable, the field silently substitutes the fallback value on `onEditingFinished` — the backend receives the fallback but the field still shows the user's original text. There is no visual indicator (red border, warning text) that substitution occurred.

The `DoubleValidator` on these fields rejects non-numeric input during typing but does not prevent submission of edge cases (empty string, bare ".", "0" parsed as falsy). A user who types "0" intending zero force gets 100 N instead with no feedback.

*Recommended UI fixes:*
1. After `onEditingFinished`, set `text` to reflect the actual submitted value: `text = (parseFloat(text) || 100.0).toFixed(1)` — so the field shows what the backend actually received.
2. Add a `color: acceptableInput ? UM.Theme.getColor("text") : UM.Theme.getColor("error")` binding on the TextField to signal rejection state visually.

*This is also a reliability concern (MED-2 in reliability review) since NaN/infinity can propagate into FEA if `math.isfinite()` backend guards are absent.*

---

### Updated Priority Table (additions from team findings)

| ID | Severity | Source | Issue |
|----|----------|--------|-------|
| UX-27 | **Critical** | physics-math-expert | TPU/brittle material warnings not shown in UI |
| UX-22 | **Critical** | code-quality-expert | Force rotation non-undoable — no user warning |
| UX-23 | **Critical** | code-quality-expert | Magnitude edits non-undoable — no user warning |
| UX-24 | **Critical** | code-quality-expert | Cancel has false affordance (solver keeps running) |
| UX-25 | **High** | performance-expert | Progress bar frozen between iterations |
| UX-26 | **Medium** | performance-expert | ETA inaccurate for OC method |

---

## 16. Cross-Discipline Findings for Team

The following items have implications beyond UI/UX and should be noted by other reviewers:

- **Physics/Math (→ physics-math-expert):** ~~Whether stress at constrained faces is artificially zeroed.~~ **Resolved by physics-math-expert:** Stress at boundary elements is the actual computed FEA stress — BCs zero fixed-node displacements but adjacent elements with mixed free/fixed nodes produce non-zero strain and physically meaningful stress. The visualization is faithful. One UX implication remains: stress concentrations at point/edge constraints are theoretically mesh-dependent (values increase with refinement). See UX-28 below.

- **Reliability (→ reliability-expert):** Advanced Settings spinboxes with hardcoded defaults (UX-10) mean the UI state does not reflect backend state. This is both a UX defect and a reliability issue — if a user sets `MinDensity=20`, applies, closes, and reopens, the spinbox shows `10` but the backend uses `20`. State is inconsistent.

- **Code Quality (→ code-quality-expert):** Hardcoded colors in ExamplesGallery (GAL-02), hardcoded strings not in i18nc (GAL-04/05), and the duplicated selection mode toolbar (UX-06) are maintainability issues. The 2400-line `BoundaryConditionPanel.qml` monolith would benefit from component extraction.

- **Testing (→ test-expert):** UI state assumptions (phase transitions, spinbox sync) should be covered by Playwright or QML test cases. The Advanced Settings initial-value bug (UX-10) would be caught by a simple round-trip test: set value → reopen → verify displayed value matches set value.

---

---

## 17. Visual Review from Screenshots

*Added 2026-04-07 after receipt of 19 screenshots from live plugin operation. All screenshots captured on macOS with UltiMaker Cura, UltiMaker S5, test model: 20×20×20 mm cube. Observations supplement and confirm static code analysis findings from Sections 1–16.*

---

### 17.1 Running Phase (Screenshot: 18.28.18)

The RUNNING phase panel displays correctly: progress bar (blue fill, ~30% complete), stage label "Solving FEA (iteration 1)... — ~10 sec remaining", and "Stop" button below. The layout is clean and uncluttered.

**Confirms UX-25:** The stage label shows only one status for the entire iteration duration — there is no sub-stage progression (Assembling → Solving → Computing Stress). The bar can be frozen at this message for 5–60 s depending on mesh complexity.

The model in the 3D viewport shows a red/purple stress gradient from a prior run's overlay that was not dismissed — this is normal Cura viewport persistence, not a plugin defect.

**VIS-01 (High) — new finding: Panel header clips behind Cura's stage toolbar.**
In this screenshot and in at least one earlier screenshot, the very top of the FEA panel's title area appears clipped or occluded behind Cura's stage selection bar (PREPARE / PREVIEW / MONITOR). The panel content begins immediately below the toolbar with no visible margin. Phase headers ("Boundary Conditions", "Analysis Results") may be partially hidden when the panel is rendered at certain window heights. This is a layout anchoring issue — the `ToolbarPanel`'s `y` or `anchors.top` may not account for the fixed toolbar height.

*Recommendation:* Audit the panel's `y` anchor / `topMargin` relative to Cura's `mainWindow.header` or equivalent toolbar item. Ensure a minimum `topMargin` that clears the stage toolbar at all supported window sizes.

---

### 17.2 Review Phase — Warning Banner (Screenshots: 18.33.02, 18.33.59, 18.34.19)

The "Unsafe" verdict banner renders correctly when fully visible: solid red background, bold white text "⚠ Unsafe: Part may fail under this load. Increase max infill or redesign." This is visually strong, appropriately alarming, and legible. The color is semantically clear.

**VIS-02 (High) — new finding: Safety-critical warning banner scrolls out of view.**
Screenshot 18.33.02 captures the panel scrolled ~30 px down: the warning banner is cropped to show only its bottom line ("...this load. Increase max infill or") with the ⊘ icon. The full red background and complete message are not visible. A user who enters the REVIEW phase with the panel in a scrolled state (from having previously viewed Advanced Settings or scrolled through DEFINE) will miss the safety-critical verdict.

Screenshot 18.33.59 (panel scrolled to top) shows the full banner correctly — the issue is scroll-state-dependent, not always present.

*Recommendation:* Either (a) place the verdict banner in a non-scrolling header region above the `ScrollView` in the REVIEW phase, or (b) programmatically reset scroll position to top when entering REVIEW: `reviewScrollView.contentItem.contentY = 0` in the `onCurrentPhaseChanged` handler. Option (a) is preferred — safety-critical content should never require scrolling to reach.

The metrics grid (Max Stress: 81.1 MPa, Min Stress: 1.0 MPa, Safety Factor: 0.74, Iterations: 2) is clearly laid out with consistent label/value columns. The SF value (0.74) is displayed without visual emphasis despite being below 1.0 — a green/amber/red color treatment on the SF value would reinforce the verdict banner.

---

### 17.3 Stress Overlay Visual Quality (Screenshots: 18.33.02, 18.33.59, 18.34.19)

The viridis stress overlay renders cleanly on the 20×20×20 mm test cube:
- Smooth purple → blue → cyan gradient from bottom (low stress) to top (high stress) ✓
- The top-left region (fixed support face) shows the highest stress in red/warm tones ✓
- No color banding artifacts or sharp discontinuities ✓
- Model geometry remains legible through the 0.85-opacity overlay ✓

**Confirms UX-16 (Critical):** The panel contains no colorbar legend anywhere. The 3D viewport also has no overlay legend. There is no mapping between the viridis colors and the numeric stress values (1.0–81.1 MPa). A user cannot determine which color corresponds to what stress level.

**Partially resolves SHADER-01:** No visible color oversaturation or "blown-out" yellow on high-stress regions at this test geometry. The additive ambient effect appears benign at this scale. SHADER-01 remains a low-severity theoretical concern.

---

### 17.4 FEA Infill Zones — Post-Apply (Screenshot: 18.34.42)

After "Apply Optimized Infill" is clicked, Cura creates three modifier mesh volumes visible in the per-model settings panel:
- "FEA Zone (39%)" — lowest density (low-stress region)
- "FEA Zone (51%)" — medium density
- "FEA Zone (62%)" — highest density (high-stress region)

The selected zone tooltip confirms correct behavior: "FEA Zone (51%) — Infill overlapping with this model is modified. Overrides 4 settings." Zone settings: Wall Thickness 0.0 mm, Top/Bottom Thickness 0.0 mm, Infill Density 50.8333%, Infill Pattern: Gyroid.

**Confirms PRO-03 (Medium):** The FEA plugin panel provides no post-apply confirmation. The user must navigate to Cura's scene/object list to discover the created zones — this transition is not indicated anywhere in the FEA panel.

**VIS-03 (Medium) — new finding: FEA Zone names show percentage but not structural context.**
"FEA Zone (39%)" is technically accurate but structurally opaque. Users who inspect the object list during a future print session cannot tell which zone corresponds to which structural region (low/medium/high stress, or support-adjacent vs. free regions). Naming like "FEA Zone — Low stress (39%)" or "FEA Zone 1 (39%, low)" would provide context without adding complexity. Additionally, the exact value "50.8333 %" (non-integer) may surprise users accustomed to round-number infill settings — a tooltip explaining this is the optimizer output would prevent confusion.

---

### 17.5 Layer View — Final Print Result (Screenshot: 18.35.12)

The PREVIEW mode layer view shows the sliced result correctly: gyroid infill fills the cube interior with visible density variation across the three FEA zones. The cyan selection border is present. Object list shows Cube + three FEA Zone modifiers. Print cost: CHF 0.06 (20 mm cube).

The end-to-end workflow produces the expected output — infill zones are correctly applied and visible in slicing. No visual issues in this view.

---

### 17.6 Observations from Earlier Screenshots (18.22.xx – 18.28.09)

The following observations were recorded during the first session from screenshots reviewed before context compaction.

**NaN in Fx/Fy/Fz input fields (confirmed, subsequently fixed):**
Screenshots captured during development showed `NaN` text in force direction fields under certain initialization conditions. This has been fixed. However, the observation confirmed the underlying `parseFloat(text) || fallback` substitution pattern (UX-29) — the fix for NaN display and the fix for silent "0" → 100 N substitution are related.

**Force gizmo position (VIS-04, Medium):**
Screenshots showed the force direction arrow gizmo rendering slightly above the model surface rather than flush with the selected face. The gizmo should anchor at the centroid of the selected face. This misregistration, even if small, creates visual ambiguity about the actual force application point — particularly when the model is viewed at oblique angles.

*Recommendation:* Verify `BoundaryConditionTool._update_handles()` positions the gizmo using the face centroid in world space, not a fixed offset from the mesh bounding box center.

**BC highlight colors confirmed correct:**
Screenshots confirmed boundary condition face highlights are correctly color-coded: green (fixed supports), red (forces), blue (torques). This matches the guide SVG color convention and the expected visual language. ✓

**Torque axis vector field (UX-08 confirmed):**
Screenshots showed the torque axis displayed as raw floats (e.g., "0.0, 0.0, 1.0"). UX-08's recommendation for a friendly-name selector (X-axis / Y-axis / Z-axis / Custom) is confirmed as the right fix.

**SVG guide diagrams (ICON-02 assessed):**
The SVG guide diagrams in the OnboardingWizard render clearly in dark-mode Cura. The `#CCCCCC` annotation text is readable against the dark panel background. ICON-02 (hardcoded text color) is low-impact in dark mode but would be a legibility issue in light-mode Cura where `#CCCCCC` text on a white/light background would fail WCAG contrast requirements.

**Material auto-detection confirmed working:**
Screenshots showed the active material from Cura's print profile (e.g., PLA, CF PET) being pre-selected in the material dropdown. This is a strong usability positive — the user does not need to manually re-enter material information that Cura already knows. ✓

**Quick Setup buttons visible and accessible:**
Screenshots confirmed Quick Setup buttons ("Gravity: Click Bottom Face", "Cantilever: Click Fixed End") are visible in the DEFINE phase and appropriately sized. HELP-05's observation that these have no help text remains valid.

**VIS-05 (Low) — QML layout loop warning (new finding):**
One screenshot captured Cura's console output showing a QML layout reflow warning consistent with the `id: forceCol` / `id: torqueCol` declarations inside `Repeater` delegates in `ExamplesGallery.qml` (GAL-03). The warning does not crash the gallery but indicates redundant layout recalculations on each gallery open. This is cosmetic/performance noise rather than a functional defect.

---

### 17.7 Summary of Visual Findings

| ID | Severity | Finding | Confirms / New |
|----|----------|---------|----------------|
| VIS-01 | **High** | Panel top clips behind Cura's stage toolbar | New |
| VIS-02 | **High** | Unsafe warning banner can scroll off-screen in REVIEW phase | New |
| UX-16 | **Critical** | No stress colorbar legend — confirmed by all Review phase screenshots | Confirmed |
| VIS-03 | **Medium** | FEA Zone names show density % but not structural context | New |
| VIS-04 | **Medium** | Force gizmo does not co-locate precisely with applied face | New |
| PRO-03 | **Medium** | No post-apply zone creation summary in FEA panel | Confirmed |
| UX-25 | **High** | Progress bar frozen per iteration — only one stage label visible | Confirmed |
| UX-29 | **Medium** | Silent numeric substitution — NaN issue was related symptom | Confirmed |
| UX-08 | **Medium** | Torque axis as raw vector — confirmed unintuitive in practice | Confirmed |
| SHADER-01 | **Low** | No over-brightness on high-stress regions at test scale | Low-impact confirmed |
| ICON-02 | **Low** | SVG text contrast OK in dark mode; would fail in light mode | Partially resolved |
| VIS-05 | **Low** | QML layout loop warning in ExamplesGallery console | New |

---

*Review complete. All findings based on static code analysis (Sections 1–16) and live screenshot review (Section 17).*

# FEA Infill Optimizer — Visual Review from Screenshots

**Reviewer:** ui-designer agent (visual pass)
**Date:** 2026-04-07
**Branch:** `worktree-streamed-prancing-biscuit`
**Source:** 19 screenshots captured live on macOS, UltiMaker Cura, UltiMaker S5, test model: 20×20×20 mm cube
**Relation:** Supplements and cross-validates the static code review in `review_ui_ux.md`

---

## Executive Summary

The plugin looks like a **professional-grade tool**, not a prototype. Dark theme integration is excellent — panel background matches Cura's sidebar exactly, typography and spacing are consistent with native Cura panels, and BC face highlight colors (green / yellow+red / purple) are vivid, visually distinct, and immediately informative. The viridis stress overlay is physically correct and aesthetically impressive. The rotation gizmo looks polished.

However, five issues stand between this and "shipped product" quality:

1. **NaN values visible in Fx/Fy/Fz/Magnitude during rotation mode** — jarring, looks like a crash
2. **No colorbar/legend on the stress overlay** — colorful but unreadable without it
3. **"Edit Boundary Cond" and "Edit Analysis Set" button labels are truncated** — unpolished
4. **Material brittleness warning is styled as muted/inactive text** — safety warning is invisible
5. **RUNNING phase panel is ~60% blank empty space** — looks broken during analysis

**Revised visual quality rating: 3.8 / 5**
(up from estimated 3.0 in code-only review; stress overlay and BC highlights are better than anticipated; NaN bug and running-phase emptiness pull it down)

---

## 1. DEFINE Phase — Supports Tab

**Screenshots: 18.22.24, 18.22.34, 18.23.03, 18.23.39**

### What Works Well

- The `Fixed Support` guide SVG (isometric block with green highlighted bottom face and anchor triangles) is clearly visible, correctly sized, and pedagogically effective. It sets correct expectations before the user clicks anything.
- The green face highlight on the cube in 18.23.03 is unmistakably clear — bright solid green on the selected bottom face, no ambiguity.
- The "Support (2 faces)" chip uses Cura's primary blue as background with white text and an × button. Matches Cura's chip pattern.
- The selection helper tooltip in 18.23.39 renders cleanly: white text on dark semi-transparent background, correctly positioned, content is good ("Single: one triangle. Surface: entire flat face. Hole: inside of a circular opening. Cylinder: outside of a round post.").
- Quick Setup buttons ("Gravity: Click Bottom Face", "Cantilever: Click Fixed End") have clean outlined styling, full-width, easily tappable.
- "Fix Bolt Holes" + spinbox row is tidy — label, spinbox, and unit label aligned cleanly in a row.

### Visual Issues

**SVIS-01 (Low): Selection mode "Single" icon is ambiguous.**
The first icon in the selection helper is a rightward-pointing triangle (play button shape). At ~24px on a dark background, it reads as "play" not "select single triangle face." The other three icons (surface/grid, circle/hole, cylinder) are more recognizable. Confirms ICON-01 from code review.

**SVIS-02 (Low): "Highlight face on hover" checkbox appears orphaned when scrolled.**
In 18.22.34, with the panel scrolled so the checkbox is at the very top of the visible area, it sits disconnected from any surrounding context — just a floating checkbox with no section it belongs to. Confirms UX-03.

**SVIS-03 (Low): Guide SVG caption ("Fixed Support") is very small.**
The label beneath the SVG renders at secondary-text size. Readable on a retina display; borderline on 1x scaling. Consider bumping to body text size.

---

## 2. DEFINE Phase — Forces Tab

**Screenshots: 18.24.11, 18.24.33, 18.24.59, 18.25.17**

### What Works Well

- The yellow face highlight for selected force faces (before confirmation) is bright and distinguishable from the green support face simultaneously present on the same model (18.24.11). The two-color display is visually clear.
- "Confirm Load on Selected Faces" — despite being flagged as verbosely labeled (UX-09), at the current font size the text fits on one line within the 280px panel without truncation. Not a practical problem at this rendering.
- The helper text "2 face(s) selected. Click 'Confirm Load' to save, or click more faces to add. Alt+click to deselect." is correctly contextual and well-positioned.

### Critical Visual Bug

**SVIS-04 (Critical): NaN values displayed in Fx/Fy/Fz/Magnitude during rotation mode.**

In screenshots 18.24.59 and 18.25.17, with rotation mode active, all four direction fields show literal "NaN" as text:
```
Fx:        NaN
Fy:        NaN
Fz:        NaN
Magnitude: NaN N
```
This is the single worst-looking visual defect in the plugin. A user glancing at the panel while dragging the rotation gizmo sees what appears to be a calculation error or crashed state. The fields should either display the current direction vector (read from the rotating gizmo's live state) or be hidden/greyed out during rotation. Related to UX-29 and the `parseFloat` substitution chain, but the NaN display in rotation mode is a distinct surface bug.

### Rotation Gizmo Quality

The three-ring rotation gizmo (green ring, red ring, with blue directional arrow) in 18.25.17 looks excellent:
- Correctly positioned, centered on/above the applied face
- Rings rendered at appropriate scale relative to the model
- Blue force arrow is large, clearly directional, highly visible against the grey grid
- The gizmo interaction banner ("Drag the rings to adjust direction. Click 'Support / Mount' or 'Apply Load' to exit.") is displayed correctly in the primary-tinted info banner style

The gizmo interaction design itself is professional-grade. The NaN side-panel values are the only detractor.

---

## 3. DEFINE Phase — Torques Tab

**Screenshots: 18.26.12, 18.26.35, 18.27.11**

### What Works Well

- The purple/violet face highlight for torque BCs is strongly distinct from green (supports) and red/yellow (forces). All three BC types are visually unambiguous. This three-color system is excellent design.
- The "Applied Torque" guide SVG shows a cylindrical shape with rotational arrows — accurate and helpful for understanding the concept.
- The amber/gold "Editing Torque 1" banner in 18.26.35 has good visual contrast against the dark panel. The color correctly signals an active edit state different from both the blue (info) and orange (warning) banners used elsewhere.
- The "Done Editing Axis" button is prominent and positioned at the top of the editing state — easy to find.
- The small blue arrow on the torque face indicates axis direction correctly.

### Visual Issues

**SVIS-05 (Medium): Raw axis vector is the only axis representation.**
In 18.26.35, the torque chip shows "Axis: [-0.707, 0.000, 0.707]". No friendly interpretation ("≈ Diagonal XZ") is provided. Confirms UX-08 — this vector is meaningless to intermediate users.

**SVIS-06 (Low): Purple torque highlight is very saturated.**
The entire cube body renders in bright magenta-purple. While distinct, for a real complex part with multiple torque BCs on adjacent faces, this saturation level could make it hard to identify individual face boundaries within the highlight. Consider desaturating slightly (to ~80% saturation) while maintaining the hue.

---

## 4. OPTIMIZE Phase — Analysis Setup

**Screenshots: 18.27.53, 18.28.09**

### What Works Well

- Material dropdown with summary line is well-executed. Pipe-separated "E = 5000 MPa | σ_yield = 60 MPa | v = 0.37 | k = 0.35" is compact and readable at this text size.
- Safety Factor spinbox displaying "2.0×" and "2.5×" is clean. The format suffix is clear.
- Radio button mesh quality selection (Fast / Balanced / Precise) is visually clean, Cura-standard, readable.
- "(recommended)" annotation in the selected infill pattern is a nice touch.
- Advanced Settings accordion with chevron opens and closes cleanly (18.28.09). Six spinboxes inside are aligned, labeled, and readable. The accordion pattern is the correct choice here.
- "Run Analysis" primary blue button is visually dominant and clearly the next action.
- "Back to Setup" secondary outlined button correctly recedes visually.

### Critical Visual Issue

**SVIS-07 (Critical): Material brittleness warning is indistinguishable from neutral metadata.**

In 18.27.53, the line "Brittle — von Mises may overestimate strength" appears in exactly the same color and weight as the technical parameter text directly above it ("E = 5000 MPa | σ_yield = 60 MPa | ..."). A user scanning this section reads it as one long technical description block. The warning is rendered in `text_inactive` — the least prominent text style in Cura's theme.

This is a safety-critical failure of visual hierarchy. The plugin has already computed that this material has a brittleness concern and surfaced it — but the visual presentation causes it to be ignored. Directly confirms UX-27.

**Fix (1 line of QML):** Apply conditional color to the warning fragment:
```qml
color: {
    var s = text
    if (s.indexOf("Hyperelastic") >= 0) return UM.Theme.getColor("error")
    if (s.indexOf("Brittle") >= 0)      return UM.Theme.getColor("warning")
    return UM.Theme.getColor("text_inactive")
}
```

### Other Issues

**SVIS-08 (Confirmed): Advanced Settings shows hardcoded defaults.**
Values in 18.28.09 are exactly 10 / 80 / 5 / 20 / 50 / Heuristic — the hardcoded values from code. Confirms UX-10. A user who previously set Min infill to 20% will see 10% on reopen.

**SVIS-09 (Low): No visual separator between required and advanced settings.**
The collapsed Advanced Settings accordion sits immediately below the Mesh Quality radios with only standard spacing. A subtle top-border or increased top-margin before the accordion would visually group "required" settings (material, infill pattern, SF, mesh quality) separately from "expert override" settings. Currently everything reads as one flat list.

---

## 5. RUNNING Phase

**Screenshot: 18.28.18**

### What Works Well

- Progress bar: blue fill on dark track, correct height, correct styling.
- Stage text "Solving FEA (iteration 1)... — ~10 sec remaining" is informative and reassuring. The inline ETA addresses UX-15 partially — the ETA is present, which is better than what the code-only review estimated.
- "Stop" button correctly styled as secondary (outlined), not competing with the progress state.

### Critical Visual Issue

**SVIS-10 (High): The RUNNING phase panel is ~60% empty.**

Below the progress bar, stage text, and Stop button, there is a large expanse of empty dark panel. The panel occupies the same full height as the DEFINE phase (which is content-dense). During analysis — 30–120 seconds typically — the user stares at a mostly-blank panel. This reads as if the UI forgot to render content, or as if the plugin crashed and the loading state is stuck.

Suggestions to fill the space meaningfully:
- Model name and dimensions (context reassurance)
- A "What's happening?" collapsible explainer: "Building a mesh of your model, then solving for stress distribution..."
- A pulsing/animated background graphic (subtle, respecting `prefers-reduced-motion`)
- The BC summary (supports: N, forces: N) as read-only review while waiting

**SVIS-11 (Medium): No iteration count context.**
"Solving FEA (iteration 1)..." shows the current iteration but not the maximum ("of 20"). A user has no mental model of progress. "Iteration 1 of 20" would take no additional space and set clear expectations.

---

## 6. REVIEW Phase

**Screenshots: 18.33.02, 18.33.59, 18.34.19**

### What Works Well

**The "Unsafe" verdict banner is the best-executed element in the entire plugin.**
In 18.33.59 (panel scrolled to top), the bright red banner with bold text "⚠ Unsafe: Part may fail under this load. Increase max infill or redesign." is impossible to miss. Correct use of error color, strong typographic weight, full-width block — this is exactly how safety-critical feedback should look.

- Metrics grid (Max Stress / Min Stress / Safety Factor / Iterations) is clean, two-column label/value layout with help icons — standard and functional.
- SF value 0.74 is correctly displayed as the computed result.
- "Apply Optimized Infill" primary blue CTA is prominent.
- "Hide Stress Map" toggle is present and functional.

### Critical Issue

**SVIS-12 (Critical): No colorbar/legend for the viridis stress overlay.**

The stress overlay on the model looks visually excellent — smooth viridis gradient from deep purple/blue at the constrained base to teal/green mid-body to bright pink/magenta near the force application face (physically correct: highest stress where the load is applied). But nowhere in the plugin panel or 3D viewport is there a legend mapping colors to stress values.

The metrics grid shows "Max Stress: 81.1 MPa" and "Min Stress: 1.0 MPa" as text — but a user cannot map any specific color to any intermediate stress value. The overlay is decorative without the legend. Directly confirms UX-16 as the highest-priority visual gap.

**Minimum viable fix:** A horizontal gradient bar immediately below the "Hide Stress Map" button:
```
[dark purple] ───────────────────── [bright yellow]
  1.0 MPa                              81.1 MPa
```
Values already available as `bcPanel.minStress` / `bcPanel.maxStress`. Only requires a `LinearGradient` fill rectangle and two labels.

### High-Priority Issue

**SVIS-13 (High): Button labels truncated in Review phase.**

Both secondary action buttons are clipped at the panel width:
- "Edit Boundary Cond|" (full: "Edit Boundary Conditions")
- "Edit Analysis Set|" (full: "Edit Analysis Settings")

The truncation is visible as cut text in 18.33.02 and 18.33.59. This looks unpolished and forces users to guess the full label.

**Fix options:**
- Abbreviate: "Edit BCs" and "Edit Settings" (fits comfortably)
- Two-line stacked text (acceptable for secondary actions)
- Or reduce font size only on these buttons to fit the full label

### Stress Overlay Visual Quality

**SVIS-14 (Medium): Visual noise / speckle artifacts at zone boundaries (18.34.19).**
At a slightly oblique angle, the stress overlay shows a speckled/dithered pattern at color transitions — particularly in the teal-to-cyan mid-stress region. This appears to be coarse-mesh vertex-color interpolation producing visible faceting (the model was run with default/Fast mesh). On a Balanced or Precise mesh this would likely smooth out. Recommend verifying: if the artifact persists on Precise mesh, a slight per-fragment smoothing in the shader would resolve it.

**SVIS-15 (Low): Near-black at the constrained base may confuse users.**
The supported base of the cube renders deep purple/near-black (lowest stress in viridis = most constrained region). Users unfamiliar with FEA stress maps may interpret dark color as "bad" or "no data." The colorbar legend (SVIS-12) would resolve this by showing black = 1.0 MPa = very low = safe.

**SVIS-16 (Low): Safety Factor value has no color encoding.**
The metrics grid shows "Safety Factor: 0.74" but the value text is rendered in the same white body text as all other metric values. A SF < 1.0 warrants at minimum a red/amber color on the value itself, reinforcing the verdict banner (which may have scrolled off). Simple conditional: `color: value < 1.0 ? UM.Theme.getColor("error") : value < sfTarget ? UM.Theme.getColor("warning") : UM.Theme.getColor("text")`.

---

## 7. Post-Apply: FEA Zones in Cura Scene

**Screenshots: 18.34.42, 18.35.12**

### What Works Well

- FEA modifier zones named correctly: "FEA Zone (39%)", "FEA Zone (51%)", "FEA Zone (62%)". Naming convention is self-explanatory.
- Tooltip in 18.34.42: "FEA Zone (51%) — Infill overlapping with this model is modified. Overrides 4 settings." — correctly informative.
- Layer view in 18.35.12 shows visually distinct infill density regions in the model cross-section. The different density zones are discernible. End-to-end workflow works and produces a verifiable result.
- Material auto-detection confirmed working: CF_PET was pre-populated from Cura's print profile without manual entry. This is a strong usability positive.

### Issues

**SVIS-17 (Medium): FEA Zone names lack structural context.**
"FEA Zone (39%)" is accurate but opaque when inspected weeks after the analysis was run. Users reviewing a saved project cannot tell which zone corresponds to which structural region. "FEA Zone — Low (39%)", "FEA Zone — Mid (51%)", "FEA Zone — High (62%)" would provide permanent context at near-zero implementation cost.

**SVIS-18 (Low): Infill density stored and displayed as "50.8333 %".**
The Infill Density value in the modifier mesh properties panel shows 7 significant figures. This looks accidental. Rounding to 1 or 2 decimal places in the plugin's output would match Cura's own convention for infill density.

**SVIS-19 (Low): No post-apply confirmation in the FEA panel.**
After clicking "Apply Optimized Infill", the FEA panel still shows the same REVIEW state with "Apply Optimized Infill" as the active primary button. There is no "Applied — 3 zones created" confirmation. The user must switch away from the FEA tool and check Cura's scene panel to verify success. Confirms UX-17.

---

## 8. Summary of All Visual Findings

### New Findings (not in code-only review)

| ID | Severity | Finding | Phase |
|----|----------|---------|-------|
| SVIS-04 | **Critical** | NaN in Fx/Fy/Fz/Magnitude during rotation mode | DEFINE / Forces |
| SVIS-13 | **High** | "Edit Boundary Cond" / "Edit Analysis Set" truncated | REVIEW |
| SVIS-10 | **High** | RUNNING phase panel ~60% empty blank space | RUNNING |
| SVIS-14 | **Medium** | Stress overlay speckle artifacts on coarse mesh | REVIEW |
| SVIS-11 | **Medium** | "iteration 1" with no total count context | RUNNING |
| SVIS-17 | **Medium** | FEA Zone names lack structural context (Low/Mid/High) | Post-apply |
| SVIS-16 | **Low** | Safety Factor value has no color encoding | REVIEW |
| SVIS-18 | **Low** | Infill density shown as "50.8333 %" | Post-apply |
| SVIS-19 | **Low** | No post-apply confirmation in FEA panel | Post-apply |

### Code Review Findings Visually Confirmed

| Code ID | Severity | Confirmation | Screenshot |
|---------|----------|-------------|-----------|
| UX-16 | **Critical** | No stress colorbar legend — confirmed, no legend visible anywhere | 18.33.59, 18.34.19 |
| UX-27 | **Critical** | Brittleness warning in `text_inactive` color — confirmed invisible as warning | 18.27.53 |
| UX-10 | **Confirmed** | Advanced Settings show hardcoded defaults (10/80/5/20/50) | 18.28.09 |
| UX-08 | **Confirmed** | Torque axis as raw vector only — confirmed unintuitive | 18.26.35, 18.27.11 |
| UX-25 | **Confirmed** | Only one stage label per iteration — confirmed | 18.28.18 |
| PRO-03 | **Confirmed** | No post-apply zone creation summary | 18.33.02 |
| ICON-01 | **Confirmed** | Triangle/play icon for "Single" selection is ambiguous | 18.22.24 |
| UX-03 | **Confirmed** | "Highlight face on hover" checkbox floats when scrolled | 18.22.34 |

### Findings That Were Better Than Estimated

| Topic | Estimated in Code Review | Actual from Screenshots |
|-------|--------------------------|------------------------|
| "Unsafe" banner prominence | Medium — hard to judge from code | Excellent — immediately visible, well-designed |
| Rotation gizmo quality | Unknown | Professional-grade, well-positioned |
| Face highlight colors | Good design, unverified | Excellent — green/yellow/red/purple system works |
| ETA in running phase | Missing (UX-15) | Present: "~10 sec remaining" shown inline |
| Material auto-detection | Assumed working | Confirmed working — CF_PET pre-populated |
| "Confirm Load" button truncation | Likely to truncate (UX-09) | Fits without truncation at current font size |
| Stress overlay fidelity | Unknown shader behavior | Physically correct gradient, smooth on standard mesh |

---

## 9. Top Fixes by Impact/Effort Ratio

**Fix these first — high visual impact, low implementation effort:**

1. **NaN display during rotation mode** — the most damaging-looking single defect. Hide or freeze the Fx/Fy/Fz fields during active gizmo rotation. ~5 lines of QML.

2. **Material warning color** — 1 line of QML. Changes "Brittle — von Mises..." from invisible to clearly warning-colored.

3. **Safety Factor color encoding** — 2–3 lines of QML. SF < 1.0 turns red, reinforcing the verdict.

4. **Button label truncation** — abbreviate "Edit BCs" and "Edit Settings". ~2 lines of QML.

5. **Colorbar legend** — ~15 lines of QML. `LinearGradient` Rectangle + two `UM.Label` min/max values. Highest UX impact per line of code of any fix in the list.

6. **Running phase content** — Add model name + BC summary as read-only text below the progress bar. ~10 lines of QML.

7. **FEA Zone names** — append structural context to zone name string when creating modifier meshes. ~3 lines of Python.

---

*Visual review complete. Based on 19 screenshots covering the full workflow from first open through layer-view result inspection.*

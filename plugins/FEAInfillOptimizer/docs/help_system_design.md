# FEA Infill Optimizer -- Contextual Help System Design

**Version:** 1.0
**Date:** 2026-04-07
**Author:** UI/UX Design (Claude Agent)
**Status:** Design Proposal (not yet implemented)

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Information Taxonomy](#2-information-taxonomy)
3. [UI Patterns for Each Type](#3-ui-patterns-for-each-type)
4. [Recommended Design](#4-recommended-design)
5. [Content Inventory](#5-content-inventory)
6. [Use Case Library / Examples Gallery](#6-use-case-library--examples-gallery)
7. [Implementation Sketch](#7-implementation-sketch)
8. [Phased Rollout](#8-phased-rollout)
9. [Accessibility Considerations](#9-accessibility-considerations)

---

## 1. Design Principles

### Users

The plugin serves two distinct user archetypes:

| Archetype | Background | Needs from help |
|-----------|-----------|-----------------|
| **Engineer** | Understands FEA, von Mises, boundary conditions | Wants quick reference for plugin-specific controls, keyboard shortcuts, and how Cura concepts map to FEA concepts |
| **Hobbyist** | Knows 3D printing but not structural analysis | Needs conceptual grounding ("What is a boundary condition?"), practical analogies ("1 kg weight = 10 N"), and guided workflows |

### Constraints

- **Panel width:** ~280px (Cura tool panel). No room for a dual-pane layout.
- **Phase-based flow:** The plugin already has 5 phases (DEFINE, OPTIMIZE, RUNNING, REVIEW, ERROR). Help must be phase-aware.
- **Existing inline guidance:** The panel already contains contextual tips (e.g., "Tip: 1 kg weight = 10 N", "Click faces where the part is held"). These are good and must be preserved -- the help system augments rather than replaces them.
- **SVG guide icons exist:** Three guide illustrations (`guide_support.svg`, `guide_force.svg`, `guide_torque.svg`) are available at `resources/icons/`.
- **No separate window:** Unlike SettingsGuide2 which opens a 1200x640 window, this help system must work within the 280px tool panel or as a lightweight overlay. A full SettingsGuide2-style window is overkill for a single plugin tool.

### Design Goals

1. **Progressive disclosure:** Start with minimal text; let users drill deeper on demand.
2. **Zero-navigation for basics:** Tooltips and inline tips require no clicks.
3. **Phase-aware relevance:** Show help relevant to the current workflow phase.
4. **Non-intrusive:** Help is discoverable but does not consume panel space by default.
5. **Diagram-capable:** Some concepts (supports, forces, torques, stress visualization) genuinely need visuals.

---

## 2. Information Taxonomy

All help content falls into six categories:

### 2.1 Quick Tooltips (T)

One to two sentences answering "What does this control do?"

- Appear on hover/focus of a "?" icon placed next to a label.
- Maximum ~120 characters.
- No images, no formatting.

**Example:**
> **Safety Factor** -- Multiplier applied to stress before comparing to material yield strength. Higher values = more conservative design.

### 2.2 How-To Guides (H)

Step-by-step instructions for completing a specific task.

- 3-7 numbered steps.
- May reference SVG diagrams.
- Scoped to a single workflow task ("How to apply a force to bolt holes").

**Example:**
> **How to apply a force to bolt holes:**
> 1. Switch to the Forces tab
> 2. Select "Hole" in the Selection helper toolbar
> 3. Click the inside of a bolt hole -- the entire hole surface highlights
> 4. Set the load amount in Newtons (Tip: 1 kg = 10 N)
> 5. Click "Confirm Load on Selected Faces"

### 2.3 Concept Explanations (C)

What a domain term means, in language accessible to non-engineers.

- 2-5 paragraphs maximum.
- May include a diagram, a formula (rendered as plain text), or a comparison table.
- Answers "What is X and why does it matter for my print?"

**Example:**
> **Von Mises Stress** -- A single number that combines all the different stresses (tension, compression, shear) acting on a point into one equivalent value. If the von Mises stress exceeds the material's yield strength, the part will deform permanently or break. The stress map on your model uses a color gradient where blue = low stress and yellow = high stress.

### 2.4 Practical Tips (P)

Best practices and recommendations. Not step-by-step, but advice.

- Short paragraphs or bullet lists.
- Answers "When should I use X?" or "What is a good value for Y?"

**Example:**
> **Choosing an infill pattern:** Gyroid (recommended) provides the best balance of strength in all directions. Use Grid or Triangles if your loads are primarily in-plane. Avoid Concentric for structural parts as it has weak radial strength.

### 2.5 Troubleshooting (E)

Diagnosis and resolution for common errors and unexpected results.

- Problem/cause/solution format.
- Linked from the ERROR phase and from specific error conditions.

**Example:**
> **"Analysis failed" with coarse mesh:** The mesh may have degenerate (zero-volume) elements. Try increasing Mesh Quality to "Balanced" or "Precise". If the model has very thin walls (<1mm), consider simplifying the geometry.

### 2.6 Use Case Examples (X)

Complete, real-world boundary condition setups for recognizable mechanical scenarios.

- Each example covers a specific part type and loading scenario.
- Shows exactly where to place supports, forces, and torques.
- Includes magnitude estimation, selection mode guidance, and key insights.
- Accompanied by an annotated SVG diagram with color-coded BC zones.
- Browsable in a dedicated gallery dialog (see Section 6).

**Example:**
> **Wall-Mount Shelf Bracket:** L-shaped bracket, fixed at the wall plate (use Surface selection on the back face), 50 N downward force on the shelf surface (5 kg x 10 N/kg). Highest stress at the L-bend corner. Use PLA, Safety Factor 2.0x, Gyroid infill.

---

## 3. UI Patterns for Each Type

### Pattern Selection Matrix

| Information Type | Primary Pattern | Secondary Pattern | Rationale |
|-----------------|----------------|-------------------|-----------|
| Quick Tooltips (T) | **A) Inline tooltip** on "?" icon | -- | Zero navigation, always available, fits the label-adjacent space |
| How-To Guides (H) | **D) Popover card** triggered from a "Guide" link | **B) Expandable section** for the step guide at the top of DEFINE phase | Popovers can show SVG diagrams without taking permanent space |
| Concept Explanations (C) | **D) Popover card** triggered from tooltip "Learn more" link | -- | Concepts need 2-5 paragraphs plus optional diagrams; a popover provides enough room without navigation |
| Practical Tips (P) | **A) Inline tooltip** or **B) Expandable section** | -- | Most tips are already inline (e.g., "Tip: 1 kg = 10 N"). Longer tips go in expandable sections |
| Troubleshooting (E) | **B) Expandable section** in ERROR phase | **D) Popover card** for specific error codes | Error guidance should be immediately visible without extra clicks when the user is frustrated |
| Use Case Examples (X) | **G) Gallery dialog** with card grid + detail view | -- | Examples need room for diagrams, multi-section BC descriptions, and recommended settings; too large for a popover or side panel |

### Pattern Details

#### A) Inline Tooltip ("?" Icon)

**Trigger:** Hover (desktop) or tap-and-hold (future touch support) on a `?` icon.

**Visual spec:**
- `?` icon: 16x16px UM.ColorImage using Cura theme's "help-contents" icon or a circled question mark
- Positioned inline after the label text, with 4px left margin
- Tooltip: Native QML `ToolTip` component (follows Cura conventions)
- Max width: 240px (narrower than panel to avoid overflow)
- Background: `UM.Theme.getColor("tooltip")` with 1px border
- Text: `UM.Theme.getFont("small")`, `UM.Theme.getColor("tooltip_text")`
- Delay: 500ms (matches SettingsGuide2 convention)
- Timeout: 10000ms (longer than default for reading)

**Accessibility:**
- The `?` icon must have `Accessible.name: "Help for [Setting Name]"` and `Accessible.role: Accessible.Button`
- Tooltip text is exposed via `ToolTip.text` which is read by screen readers
- Focus-accessible via Tab key (the `?` icon is keyboard-focusable)
- Touch target: 24x24px minimum hit area (padded from the 16px icon) -- note: this is below the 44px recommendation, but acceptable because the `?` icon is a secondary disclosure affordance adjacent to a larger interactive element. The primary interactive element (the setting control itself) meets the 44px minimum.

#### B) Expandable Help Section

**Trigger:** Click/tap on a collapsible header row (chevron icon toggles).

**Visual spec:**
- Header: Row with `UM.Label` (text like "How it works" or "Tips") + chevron icon (ChevronSingleDown/Up)
- Header height: 32px minimum (meets touch target with full-width click area)
- Expanded content: Indented by `default_margin`, background color `detail_background` with `default_radius` corner radius
- Content: `UM.Label` with `wrapMode: Text.WordWrap`, `font: small`
- Animation: Height transition, 150ms ease-in-out, respects `prefers-reduced-motion` (instant if reduced)

**Accessibility:**
- Header acts as a button: `Accessible.role: Accessible.Button`
- ARIA expanded state: `Accessible.name: "Tips, expanded"` or `"Tips, collapsed"`
- Content region: `Accessible.role: Accessible.StaticText`

#### D) Popover Card

**Trigger:** Click on a "Learn more" link within a tooltip, or click on a "Guide" icon/link.

**Visual spec:**
- Appears as a `Popup` or `Dialog` component overlaying the tool panel
- Width: 260px (slightly narrower than panel to show edges)
- Max height: 400px with internal `ScrollView`
- Background: `main_background` with `default_lining` border and drop shadow (4px offset, 0.15 opacity)
- Header: Bold title + close button (X icon, 24x24px, Accessible.name: "Close help")
- Body: Rich text content (`Text.RichText` or `Text.StyledText` for bold/italic)
- Images: SVG rendered via `Image` component, max-width 100%, auto-height
- Footer: Optional "Was this helpful?" (future) or related topic links
- Dismiss: Click outside, press Escape, or click close button
- Position: Anchored to the triggering element, positioned to avoid overflow

**Accessibility:**
- Focus trap while open (Tab cycles within the popover, Escape closes)
- `Accessible.role: Accessible.Dialog`
- Close button: `Accessible.name: "Close"`, keyboard-accessible
- SVG images: Each has descriptive alt text via `Accessible.description`

#### E) Onboarding Wizard (First-Run Only)

**Trigger:** Automatically shown the first time the FEA tool is activated. Tracked via a Cura preference (`fea_optimizer/onboarding_completed`).

**Visual spec:**
- Modal overlay with step indicator (dots: 1/4, 2/4, etc.)
- Each step shows one SVG diagram + 2-3 sentences + "Next" / "Skip" buttons
- Width: 300px, centered over the viewport
- "Don't show again" checkbox on last step
- Steps: (1) What this tool does, (2) Define supports, (3) Apply forces, (4) Run and review

**Accessibility:**
- Focus trap while open
- Step indicator: `Accessible.name: "Step 2 of 4"`
- "Skip" and "Next" buttons meet 44x44px minimum
- "Don't show again" checkbox: `Accessible.name` includes the label text

---

## 4. Recommended Design

### 4.1 Architecture Overview

The help system combines five patterns in a layered approach:

```
Layer 1 (Always visible):   Inline tips already in the panel (preserved as-is)
Layer 2 (Zero-click):       "?" tooltip icons next to every non-obvious label
Layer 3 (One-click):        Popover cards for guides and concepts
Layer 4 (First-run only):   Onboarding wizard (4 steps, shown once)
Layer 5 (On-demand):        Examples Gallery dialog (browsable use-case library)
```

### 4.2 Per-Phase Help Integration

#### DEFINE Phase

```
+---------------------------------------------+
| FEA Infill Optimizer               [Examples]|
|                                              |
| Quick Setup                                  |
|   [Gravity: Click Bottom Face] [?]           |
|   [Cantilever: Click Fixed End] [?]          |
|   [Fix Bolt Holes] [8.00 mm] [?]             |
|                                              |
| [Supports] [Forces] [Torques]   [? Guide]   |
|                                              |
| (tab content with inline tips as today)      |
|                                              |
| Selection helper                    [?]      |
|   [Single] [Surface] [Hole] [Cylinder]       |
|                                              |
| Supports                                     |
|   "No supports defined. Click faces..."      |
|   Or browse [View Examples] for setups       |
|                                              |
|                   [Confirm and Optimize]      |
+---------------------------------------------+
```

Key additions:
- **"Examples" link** in the panel header -- opens the Examples Gallery dialog from any phase
- **"View Examples" link** in the empty-state text -- visible when no BCs are defined, provides a direct entry point for beginners
- **"?" icons** next to: Quick Setup buttons, Selection helper label, tab headers
- **"? Guide" link** in the tab bar area -- opens a popover with the relevant SVG guide (support/force/torque) and step-by-step instructions
- **Expandable "How it works" section** below the step guide (visible only when no BCs are defined)

The existing step guide text (lines 106-111 in BoundaryConditionPanel.qml) is preserved as Layer 1.

#### OPTIMIZE Phase

```
+---------------------------------------------+
| Analysis Setup                               |
|                                              |
| [2 support(s), 1 force(s)]         [Edit]    |
|                                              |
| Material                            [?]      |
|   [PLA ▾]                                    |
|   E=3000 MPa, sigma_yield=50 MPa            |
|   "Select your printing material..."         |
|                                              |
| Infill Pattern                       [?]      |
|   [Gyroid (recommended) ▾]                    |
|   "Pattern used in infill zones..."           |
|                                              |
| Safety Factor                        [?]      |
|   [2.0x]  "Higher margin = more..."           |
|                                              |
| Mesh Quality                         [?]      |
|   ( ) Fast   (x) Balanced   ( ) Precise      |
|                                              |
| > Advanced Settings                  [?]      |
|   (collapsed by default)                     |
|                                              |
|                         [Run Analysis]        |
|                         [Back to Setup]       |
+---------------------------------------------+
```

Key additions:
- **"?" icons** next to: Material, Infill Pattern, Safety Factor, Mesh Quality, Advanced Settings
- Material "?" tooltip: "Your printing material determines stiffness (E) and strength (sigma_yield). Stiffer materials resist bending; stronger materials resist breaking."
- Infill Pattern "?" popover: Comparison table of all patterns with stiffness scaling behavior
- Safety Factor "?" tooltip: "Ratio of material strength to max allowed stress. 2.0x means the part can handle 2x the applied load before yielding."
- Advanced Settings section already has its own expandable pattern. Each advanced setting gets a "?" tooltip.

#### RUNNING Phase

No help additions needed. The phase is transient and already shows stage labels. The progress bar and stage text (e.g., "Tetrahedralizing mesh...", "Solving FEA system...", "Computing density field...") serve as implicit help.

#### REVIEW Phase

```
+---------------------------------------------+
| Analysis Results                             |
|                                              |
| [HIGH / MED / LOW mesh quality badge]        |
|                                              |
| [SAFE / UNSAFE / MARGINAL verdict]    [?]    |
|                                              |
| Max Stress:    12.4 MPa              [?]     |
| Min Stress:     0.1 MPa                      |
| Safety Factor:  4.03                 [?]     |
| Iterations:     5                    [?]     |
|                                              |
|              [Apply Optimized Infill]        |
|              [Show/Hide Stress Map]   [?]    |
|  [Edit BCs]  [Edit Analysis Settings]        |
|              [Clear Results]                 |
+---------------------------------------------+
```

Key additions:
- **"?" icon** next to the verdict chip: Popover explaining what each verdict means and what the user should do about it
- **"?" icons** next to Max Stress, Safety Factor, Iterations: Tooltips explaining what these numbers mean
- **"?" icon** next to Stress Map toggle: Tooltip explaining the color scale (viridis: dark purple = low, yellow = high)

#### ERROR Phase

The error phase already contains recovery suggestions (lines 2057-2062). Enhancement:

- Add an **expandable "Common Issues" section** below the error message with 3-5 troubleshooting entries
- Each entry: problem title (clickable to expand) with cause and resolution

### 4.3 The "? Guide" Popover (Core Component)

This is the most significant new UI element. It is a reusable popover card that can display rich help content.

**Trigger points:**
- "? Guide" link next to each BC tab (Supports/Forces/Torques)
- "?" icons that link to extended explanations (Material, Infill Pattern, verdict)
- "Learn more" text appended to certain tooltips

**Content structure per guide:**

```
+------------------------------------------+
| [X]                                      |
|  Fixed Supports                          |
|                                          |
|  [guide_support.svg illustration]        |
|                                          |
|  Fixed supports prevent faces from       |
|  moving during analysis. They simulate   |
|  where your part is held, screwed down,  |
|  or resting on a surface.               |
|                                          |
|  Steps:                                  |
|  1. Click faces where the part is held   |
|  2. Each click adds a face (Alt+click    |
|     to deselect)                         |
|  3. Use Selection helper for multi-face  |
|     surfaces                             |
|                                          |
|  Tips:                                   |
|  - At least one support is required      |
|  - Supports and forces must be on        |
|    different faces                        |
|  - Use "Surface" mode to select an       |
|    entire flat face with one click       |
+------------------------------------------+
```

### 4.4 Onboarding Wizard (First-Run)

Shown once when the FEA tool is first activated. Four steps:

| Step | Title | SVG | Content |
|------|-------|-----|---------|
| 1 | "What does this tool do?" | (none, or a simple infill-zones diagram) | "This tool analyzes structural loads on your model and optimizes infill density zone-by-zone. Regions under high stress get denser infill; low-stress regions stay light." |
| 2 | "Step 1: Define Supports" | `guide_support.svg` | "Click faces where your part is held, mounted, or resting. These faces will not move during the simulation." |
| 3 | "Step 2: Apply Forces" | `guide_force.svg` | "Click faces where forces act (weight, push, pull). Set the load amount in Newtons. Tip: 1 kg of weight is about 10 N." |
| 4 | "Step 3: Run and Review" | (none) | "Click 'Confirm and Optimize', choose your material, and run the analysis. The tool will suggest safe infill zones you can apply with one click." |

Bottom of step 4: `[x] Don't show this again` + `[Get Started]`

---

## 5. Content Inventory

### 5.1 DEFINE Phase Content

| ID | Topic | Type(s) | Trigger Location | Content Summary |
|----|-------|---------|-------------------|-----------------|
| D01 | Fixed supports | T + H | "?" next to Supports tab; "? Guide" popover | "Faces that cannot move during analysis. Simulate mounting points, screws, or resting surfaces." Steps: click faces, use selection helpers, Alt+click to deselect. SVG: `guide_support.svg` |
| D02 | Force application | T + H + C | "?" next to Forces tab; "? Guide" popover | "Apply loads (weight, push, pull) to faces. Set magnitude in Newtons." Steps: select faces, set amount, confirm. SVG: `guide_force.svg`. Concept: force distribution across selected faces. |
| D03 | Torque application | T + H + C | "?" next to Torques tab; "? Guide" popover | "Apply rotational loads (twisting) to faces. Set magnitude in Newton-meters." Steps: select faces, set amount, optionally edit axis direction. SVG: `guide_torque.svg`. Concept: torque axis, how torques differ from forces. |
| D04 | Quick Setup: Gravity | T + H | "?" next to Gravity button | "One-click setup for parts under their own weight. Click the bottom face and the tool automatically fixes the bottom and applies downward force to the top." |
| D05 | Quick Setup: Cantilever | T + H | "?" next to Cantilever button | "One-click setup for a fixed beam. Click the clamped end; the tool fixes it and loads the opposite end." |
| D06 | Quick Setup: Bolt Holes | T + H | "?" next to Fix Bolt Holes button | "Auto-detect circular holes below a diameter threshold and mark them as fixed supports. Useful for parts screwed down through bolt holes." |
| D07 | Selection helpers | T | "?" next to Selection helper label | "Single: one triangle at a time. Surface: entire flat face. Hole: inside of a circular opening. Cylinder: outside of a round post." |
| D08 | Selection: Single mode | T | Tooltip on Single button | "Select individual mesh triangles. Use for precise control." |
| D09 | Selection: Surface mode | T | Tooltip on Surface button | "Select all connected coplanar triangles forming a flat face. One click selects an entire flat surface." |
| D10 | Selection: Hole mode | T | Tooltip on Hole button | "Select the inside wall of a circular hole or pocket. Useful for bolt-hole mounting." |
| D11 | Selection: Cylinder mode | T | Tooltip on Cylinder button | "Select the outside wall of a cylindrical boss or post. Useful for pin loads." |
| D12 | Alt+click behavior | T | Inline tip (already exists) | "Alt+click (Option on Mac) toggles a face: removes it if already selected, adds it if not." |
| D13 | Face hover preview | T | "?" next to hover preview checkbox | "When enabled, hovering over faces highlights them before clicking. Helps you see which triangles will be selected." |
| D14 | Force direction (Fx/Fy/Fz) | T + C | "?" next to Direction (advanced) label | "Force components in the X, Y, and Z axes. The magnitude is sqrt(Fx^2 + Fy^2 + Fz^2). For most cases, use the magnitude field above and let the direction default to the face normal." |
| D15 | Force rotation gizmo | T | Tooltip or rotate-mode banner | "Drag the colored rings to rotate the force direction. Red ring = rotate around X, Green = Y, Blue = Z." |
| D16 | Torque axis editing | T + C | "? Guide" in torque tab | "The torque axis defines the line around which the twist acts. By default it is the average surface normal of selected faces. Click 'Edit Axis' and drag the rotation rings to change it." |
| D17 | Confirm Load button | T | Tooltip on Confirm Load button | "Saves the current face selection and force settings as a named force group. You can define multiple force groups for different load cases." |
| D18 | Editing existing BCs | T | Tooltip on list items | "Click a force/torque entry to select it. Modify the magnitude or faces, then click 'Apply Changes'. Click the X to delete." |

### 5.2 OPTIMIZE Phase Content

| ID | Topic | Type(s) | Trigger Location | Content Summary |
|----|-------|---------|-------------------|-----------------|
| O01 | Material selection | T + C | "?" next to Material label | "Your printing material determines how stiff and strong the part is. Stiffness (E) resists bending; strength (sigma_yield) resists breaking. The analysis uses these values to compute stress and safety." |
| O02 | Material: PLA | P | Tooltip on PLA in dropdown or popover | "PLA: Stiff but brittle. E=3000 MPa, yield=50 MPa. Good for rigid parts that don't need impact resistance." |
| O03 | Material: ABS | P | Tooltip or popover | "ABS: Less stiff, more ductile than PLA. E=2100 MPa, yield=35 MPa. Better impact resistance, but weaker in tension." |
| O04 | Material: PETG | P | Tooltip or popover | "PETG: Good all-rounder. E=2000 MPa, yield=42 MPa. Balances stiffness, strength, and layer adhesion." |
| O05 | Material: Nylon | P | Tooltip or popover | "Nylon (PA6): Flexible, strong, excellent layer bonding. E=1400 MPa, yield=48 MPa. Values for conditioned state (50% humidity); dry parts are ~20% stiffer." |
| O06 | Material: PC | P | Tooltip or popover | "PC (Polycarbonate): Very strong, good impact resistance. E=2200 MPa, yield=60 MPa. Difficult to print but excellent mechanical properties." |
| O07 | Material: TPU 95A | P + E | Tooltip or popover (with warning) | "TPU 95A: Flexible elastomer. WARNING: Linear elastic FEA is not valid for hyperelastic materials. Results are approximate only and must not be used for structural assessment." |
| O08 | Material: CF-Nylon | P | Tooltip or popover | "CF-Nylon: Carbon-fiber reinforced nylon. Very stiff in XY (E=6500 MPa), weaker in Z due to fiber alignment. Excellent for lightweight structural parts." |
| O09 | Infill pattern | T + P | "?" next to Infill Pattern label; popover with comparison table | "The infill pattern determines how stiffness scales with density. Stretching-dominated patterns (lines, triangles) scale linearly; bending-dominated patterns (grid, honeycomb) scale with the square." |
| O10 | Infill: Gyroid | P | Within infill popover | "Gyroid: Recommended. Isotropic stiffness (equal in all directions). Exponent 1.6 -- good balance between weight savings and strength." |
| O11 | Infill: Grid | P | Within infill popover | "Grid: Bending-dominated. Exponent 2.0 -- needs higher density to achieve the same stiffness as gyroid. Strong in-plane but weaker than gyroid in off-axis loading." |
| O12 | Safety factor | T + C | "?" next to Safety Factor label | "The safety factor divides the material's yield strength to get the 'design stress'. At SF=2.0x, the part is designed to handle 2x the applied load. Typical values: 1.5x (lightweight/non-critical), 2.0x (general purpose), 3.0x (safety-critical)." |
| O13 | Mesh quality / resolution | T + C | "?" next to Mesh Quality label | "Controls the density of the tetrahedral mesh used for FEA. Coarse: ~5000 elements (fast, less accurate). Balanced: ~15000 elements (good tradeoff). Fine: ~50000 elements (precise, slower). Use fine for thin-walled or geometrically complex parts." |
| O14 | Advanced: Min/Max infill | T | "?" next to each advanced field | "Min infill: lowest density any zone can have (default 10%). Max infill: highest density (default 80%). The optimizer distributes material between these bounds." |
| O15 | Advanced: Density steps | T | "?" next to Density steps | "Number of discrete infill density zones. More steps = smoother gradation but more modifier meshes. 3-5 is typical." |
| O16 | Advanced: Analysis passes | T + C | "?" next to Analysis passes | "Number of optimization iterations. Each pass re-solves the FEA system with updated densities and re-distributes material. More passes = better convergence but longer analysis time." |
| O17 | Advanced: Layer bonding | T + C | "?" next to Layer bonding | "Percentage representing how well layers stick together. 100% = perfect bonding (isotropic). 50% = Z-direction stiffness is half of XY stiffness. Affects the anisotropic material model. Lower values account for layer adhesion weakness." |
| O18 | Advanced: Optimization method | T + C | "?" next to Optimization dropdown | "Heuristic: Maps stress directly to density using a power law. Simple, fast, no volume target. SIMP OC: Topology optimization using Optimality Criteria. Controls total material usage via a volume fraction target. More rigorous but slower." |
| O19 | Advanced: Target volume | T | "?" next to Target volume (visible when SIMP OC selected) | "Fraction of the total model volume to fill with material. 50% means the optimizer aims for half-solid infill on average. Lower = lighter but may compromise safety." |
| O20 | Dependency warning | E | Inline (already exists as banner) | "Some Python libraries required for FEA are not installed. Click 'Install Dependencies' to download them. Cura must be restarted after installation." |

### 5.3 REVIEW Phase Content

| ID | Topic | Type(s) | Trigger Location | Content Summary |
|----|-------|---------|-------------------|-----------------|
| R01 | Safety verdict | T + C | "?" next to verdict chip | "Compares peak stress to material yield strength divided by safety factor. SAFE: max stress well below limit. MARGINAL: close to the limit. UNSAFE: exceeds the limit -- part may fail. CONSERVATIVE: stress is very low -- you could reduce infill to save material." |
| R02 | Max stress (von Mises) | T + C | "?" next to Max Stress metric | "Peak von Mises equivalent stress in the model, in MPa. Von Mises stress combines tension, compression, and shear into one number. If this exceeds (yield strength / safety factor), the verdict is 'unsafe'." |
| R03 | Safety factor result | T | "?" next to Safety Factor metric | "Ratio of material yield strength to peak stress. Values >1.0 mean the part should survive; <1.0 means likely failure. This is the *actual* safety factor based on analysis results, not the *target* you set." |
| R04 | Convergence iterations | T | "?" next to Iterations metric | "Number of optimization passes completed before the density field stabilized. If this equals the maximum allowed (set in Advanced), the result may not be fully converged -- consider increasing the limit." |
| R05 | Mesh quality badge | T + C | "?" on the mesh quality indicator | "HIGH: Used Gmsh tetrahedralization -- most accurate. MEDIUM: Used fallback meshing -- moderate accuracy. LOW: Approximate mesh -- increase safety margin to compensate. Mesh quality depends on model geometry; thin walls and sharp features may require finer settings." |
| R06 | Stress map visualization | T + C | "?" next to Show/Hide Stress Map button | "Overlays a color map on the model showing stress distribution. Uses a viridis-style colorblind-safe gradient: dark purple = zero stress, blue/green = moderate, yellow = maximum stress. Compare colored regions to your boundary conditions to verify the analysis makes physical sense." |
| R07 | Apply Optimized Infill | T + H | "?" next to Apply button | "Creates modifier meshes in Cura that set different infill densities per zone. Each zone appears as a child mesh under your model in the object list. You can manually adjust zone densities after applying." |
| R08 | Clear Results | T | Tooltip on Clear Results button | "Removes all FEA results, stress overlays, and modifier meshes. Your boundary condition definitions (supports, forces, torques) are preserved." |

### 5.4 ERROR Phase Content

| ID | Topic | Type(s) | Trigger Location | Content Summary |
|----|-------|---------|-------------------|-----------------|
| E01 | Analysis failed | E | Expandable section in error phase | "Common causes: (1) Mesh too coarse for geometry -- try Balanced or Fine mesh quality. (2) Supports and forces on the same face -- they must be on different faces. (3) No supports defined -- at least one face must be fixed. (4) Degenerate geometry -- very thin walls or self-intersecting mesh." |
| E02 | Singular stiffness matrix | E | Expandable in error phase | "The FEA system could not be solved because the model is unconstrained. This usually means supports are insufficient. Ensure at least one face is fully fixed (all 3 directions constrained). Try adding supports on a second face to prevent rotation." |
| E03 | Dependency install failed | E | Banner in optimize phase | "Library installation failed. Check your internet connection. If behind a proxy, pip may need proxy configuration. As a workaround, manually install numpy, scipy, and gmsh in Cura's Python environment." |
| E04 | Out of memory | E | Expandable in error phase | "The model has too many elements for available memory. Try: (1) Use 'Fast (coarse)' mesh quality, (2) Simplify the model geometry, (3) Close other applications to free memory." |
| E05 | TPU/hyperelastic warning | E | Inline warning in optimize phase | "TPU 95A is a hyperelastic material. The linear elastic solver used by this plugin cannot accurately predict its behavior. Results are approximate. For critical applications, use specialized nonlinear FEA software." |

### 5.5 Cross-Phase / General Content

| ID | Topic | Type(s) | Trigger Location | Content Summary |
|----|-------|---------|-------------------|-----------------|
| G01 | What is FEA? | C | Onboarding step 1; "Learn more" link in panel header | "Finite Element Analysis divides your model into thousands of tiny tetrahedra (3D triangles), applies physics equations to each, and solves for stress and deformation. This plugin uses FEA to figure out which regions of your model need more or less infill." |
| G02 | What are boundary conditions? | C | Onboarding; "? Guide" first use | "Boundary conditions are the constraints you place on your model: where it's held (supports) and where forces act (loads). Without boundary conditions, the simulation doesn't know how the part is used." |
| G03 | Newtons and force units | P | Inline tip (already exists) + tooltip | "Force is measured in Newtons (N). 1 kg of weight exerts about 10 N of force. A finger push is about 20-50 N. A person standing on a part is about 700-1000 N." |
| G04 | Newton-meters and torque | P | Inline tip (already exists) + tooltip | "Torque is measured in Newton-meters (Nm). Hand-tightened bolt: 1-5 Nm. Wrench-tightened bolt: 10-50 Nm. Car wheel lug nut: 90-120 Nm." |
| G05 | Young's modulus (E) | C | "Learn more" in material popover | "Young's modulus measures material stiffness -- how much it resists bending or stretching. Higher E = stiffer material. PLA (E=3000 MPa) is about 2x stiffer than Nylon (E=1400 MPa). In-plane (XY) stiffness is typically higher than through-layer (Z) stiffness in 3D printed parts due to layer adhesion." |
| G06 | Yield strength (sigma_yield) | C | "Learn more" in material popover | "Yield strength is the stress at which the material permanently deforms (bends and doesn't spring back). Below yield: elastic (springs back). Above yield: plastic deformation or fracture." |
| G07 | Poisson's ratio (nu) | C | "Learn more" in material popover (advanced) | "Poisson's ratio describes how much a material squeezes sideways when you push on it. Typical range 0.3-0.4 for plastics. Values near 0.5 (like TPU at 0.48) indicate near-incompressible behavior." |
| G08 | SIMP OC optimization | C | "Learn more" link in Advanced Settings popover | "SIMP (Solid Isotropic Material with Penalization) is a topology optimization method. It iteratively redistributes material to minimize structural compliance (maximize stiffness) while meeting a volume target. The Optimality Criteria (OC) update rule adjusts element densities based on strain energy sensitivities." |
| G09 | Homogenization exponents | C | "Learn more" in infill pattern popover | "Each infill pattern has a scaling exponent that describes how stiffness changes with density. Lines (exponent 1.0) scale linearly -- double the density, double the stiffness. Grid (exponent 2.0) scales quadratically -- you need 4x the density to double the stiffness. Gyroid (1.6) is in between." |
| G10 | Tsai-Hill criterion | C | "Learn more" in results popover (when anisotropic) | "The Tsai-Hill failure criterion extends von Mises stress to anisotropic materials. It accounts for different strengths in different directions (XY vs Z in a 3D print). Values above 1.0 indicate likely failure." |
| G11 | Convergence | C | "Learn more" next to iterations metric | "Convergence means the optimization has stabilized -- further iterations would not significantly change the density distribution. If the optimizer uses all allowed passes without converging, try increasing 'Analysis passes' or loosening the density bounds." |

---

## 6. Use Case Library / Examples Gallery

### 6.1 Purpose and Value

The Use Case Library is a browsable collection of real-world boundary condition setups that answer the most common question hobbyists face: "I have a part -- how do I set up the analysis?" Rather than teaching abstract concepts, examples show concrete face selections, force values, and expected outcomes for recognizable mechanical scenarios.

**Target user:** Primarily hobbyists who can recognize their scenario in a gallery but would not know how to derive the setup from first principles. Engineers also benefit from quick-start templates.

### 6.2 UI Pattern: Card Gallery in a Dialog

The examples library is too content-rich for a 280px tool panel popover. It requires its own dialog window -- but a lightweight one, not a full SettingsGuide2-scale application.

**Access point:** A "View Examples" button in the DEFINE phase step guide (shown when no BCs are defined), and a persistent "Examples" link in the panel header area accessible from any phase.

```
+---------------------------------------------+
| FEA Infill Optimizer               [Examples]|
|                                              |
| Quick start:                                 |
| 1. Select 'Support / Mount' and click...     |
| ...                                          |
| Or browse [View Examples] for common setups  |
+---------------------------------------------+
```

**Dialog spec:**

```
+================================================================+
| Example Setups                                          [X]    |
|================================================================|
|                                                                |
| [Search: ________________]  [Filter: All / Bracket / ...]     |
|                                                                |
| +---------------------------+  +---------------------------+  |
| | [SVG diagram]             |  | [SVG diagram]             |  |
| |                           |  |                           |  |
| | Wall-Mount Shelf Bracket  |  | Robot Arm Joint Bracket   |  |
| | Cantilever with gravity   |  | Fixed + Force + Torque    |  |
| |                           |  |                           |  |
| | Difficulty: Beginner      |  | Difficulty: Intermediate  |  |
| | [View Setup >]            |  | [View Setup >]            |  |
| +---------------------------+  +---------------------------+  |
|                                                                |
| +---------------------------+  +---------------------------+  |
| | [SVG diagram]             |  | [SVG diagram]             |  |
| |                           |  |                           |  |
| | Motor Mount Housing       |  | Bolt-On Handle / Lever    |  |
| | Vibration + bolt holes    |  | Grip force + pivot        |  |
| |                           |  |                           |  |
| | Difficulty: Intermediate  |  | Difficulty: Beginner      |  |
| | [View Setup >]            |  | [View Setup >]            |  |
| +---------------------------+  +---------------------------+  |
|                                                                |
+================================================================+
```

**Visual spec:**
- Window: 700x500px (smaller than SettingsGuide2's 1200x640)
- Layout: 2-column card grid with scrolling
- Each card: ~320x200px, containing:
  - SVG diagram (120px height) showing the part with highlighted BCs
  - Title (bold, 14pt)
  - Subtitle (one-line BC summary)
  - Difficulty badge (Beginner / Intermediate / Advanced)
  - "View Setup" link (opens the detail view within the same dialog)
- Search/filter bar at top: text search + category dropdown
- Cards use `main_background` with `default_lining` border, `default_radius` corners
- Hover: Subtle elevation increase (border color changes to `primary`)

**Detail view (single example):**

```
+================================================================+
| [< Back]  Robot Arm Joint Bracket                       [X]    |
|================================================================|
|                                                                |
|  [Large SVG diagram showing the part with all BCs annotated]  |
|                                                                |
|  SCENARIO                                                      |
|  Structural bracket connecting a servo gearbox to a robot      |
|  arm's base frame. The bracket must handle both the arm's      |
|  weight (vertical force) and the servo's output torque         |
|  (rotational load through the flange bolt circle).             |
|                                                                |
|  +-- FIXED SUPPORTS (green) --------------------------+       |
|  | Where: Upper flat plate (bolted to base frame)      |       |
|  | Selection mode: Surface (click the top flat face)   |       |
|  | Why: These bolts hold the bracket to the frame      |       |
|  +----------------------------------------------------+       |
|                                                                |
|  +-- APPLIED FORCE (red) -------- 50 N downward -----+       |
|  | Where: Gearbox seat hole (inner wall)               |       |
|  | Selection mode: Hole (click inside the bore)        |       |
|  | Direction: -Y (downward, arm weight + payload)      |       |
|  | How to estimate: arm mass 0.5kg + payload 4.5kg     |       |
|  |                  = 5 kg x 10 = 50 N                 |       |
|  +----------------------------------------------------+       |
|                                                                |
|  +-- APPLIED TORQUE (blue) ------- 5 Nm ----axis: Z --+       |
|  | Where: Circular flange bolt circle (NOT the hole    |       |
|  |        inner wall -- torque acts through the bolts,  |       |
|  |        not through the bore)                         |       |
|  | Selection mode: Surface (click the flat flange face) |       |
|  | Axis: Z (perpendicular to the flange face)          |       |
|  | Key insight: Select the flange FACE, not the bore   |       |
|  +----------------------------------------------------+       |
|                                                                |
|  EXPECTED RESULTS                                              |
|  - High stress at the bracket's narrow neck between the       |
|    mounting plate and the gearbox seat                          |
|  - Moderate stress around bolt holes                           |
|  - Low stress on the flat mounting plate                       |
|                                                                |
|  RECOMMENDED SETTINGS                                          |
|  Material: CF-Nylon or PETG | Safety Factor: 2.5x             |
|  Mesh Quality: Balanced | Infill Pattern: Gyroid               |
|                                                                |
|                                          [Apply This Setup]    |
+================================================================+
```

**Detail view visual spec:**
- Full dialog width, scrollable vertically
- SVG diagram: ~300px wide, centered, showing the part with color-coded BC zones
- BC sections: Colored left-border strips (green for supports, red for forces, blue for torques) -- colors match the plugin's existing BC highlight colors
- "Apply This Setup" button: Optional future feature that auto-populates the Quick Setup with the example's parameters. For MVP, this button is omitted -- the user follows the instructions manually.

### 6.3 Example Entries

#### EX01: Wall-Mount Shelf Bracket (Beginner)

| Field | Value |
|-------|-------|
| **Scenario** | L-shaped bracket screwed to a wall, supporting a shelf. Classic cantilever bending. |
| **Fixed supports** | Back plate (the face against the wall, where screws go). Use Surface mode to select the entire flat back. |
| **Force** | Downward force on the top shelf-bearing surface. Estimate: shelf load 5 kg = 50 N. Direction: -Y (straight down). |
| **Torque** | None. |
| **Key insight** | The highest stress occurs at the bend between the wall plate and the shelf arm. If the bracket fails, it will fail here. Increase infill density in this region. |
| **Recommended settings** | Material: PLA or PETG. Safety Factor: 2.0x. Mesh: Balanced. |
| **Expected stress pattern** | Stress concentration at the inner corner of the L-bend. Low stress on the flat plates. |
| **Difficulty** | Beginner |
| **Category** | Bracket |

#### EX02: Robot Arm Joint Bracket (Intermediate)

| Field | Value |
|-------|-------|
| **Scenario** | Structural bracket connecting a servo gearbox to a robot arm base. Handles vertical load (arm weight) and rotational load (servo torque). |
| **Fixed supports** | Upper flat plate (bolted to base frame). Use Surface mode. |
| **Force** | 50 N downward on gearbox seat hole inner wall. Use Hole mode. Represents arm mass (0.5 kg) + payload (4.5 kg) = 5 kg at 10 N/kg. |
| **Torque** | 5 Nm on circular flange face (NOT the hole inner wall). Use Surface mode on the flange face. Axis: perpendicular to flange (typically Z). Represents servo output torque. |
| **Key insight** | The torque face selection is the flange bolt circle surface, NOT the hole inner wall. Torque transfers through the bolt pattern on the flat face, not through the bore. |
| **Recommended settings** | Material: CF-Nylon or PETG. Safety Factor: 2.5x. Mesh: Balanced or Fine. |
| **Expected stress pattern** | Highest stress at the bracket's narrow neck between the mounting plate and gearbox seat. Moderate stress around bolt holes. |
| **Difficulty** | Intermediate |
| **Category** | Bracket, Robotics |

#### EX03: Gear Housing / Motor Mount (Intermediate)

| Field | Value |
|-------|-------|
| **Scenario** | Enclosure for a small DC motor or gearbox. The housing must resist vibration forces and torque reaction from the motor. |
| **Fixed supports** | Bottom plate or mounting feet. If feet have bolt holes, use Fix Bolt Holes quick setup (set diameter to match your screws). |
| **Force** | Motor vibration: small oscillating force (5-10 N) on the motor seat. Direction: perpendicular to the motor axis. |
| **Torque** | Motor reaction torque on the motor seat ring. Typical small motor: 0.5-2 Nm. Axis: along the motor shaft. |
| **Key insight** | Motor vibration forces are cyclic. Use a higher safety factor (3.0x) to account for fatigue. The linear FEA cannot model fatigue directly, but a higher safety factor provides margin. |
| **Recommended settings** | Material: PETG or Nylon (for vibration damping). Safety Factor: 3.0x. Mesh: Balanced. |
| **Expected stress pattern** | Stress around motor seat and bolt holes. Low stress on housing walls (unless thin). |
| **Difficulty** | Intermediate |
| **Category** | Enclosure, Motor Mount |

#### EX04: Structural Beam with Distributed Load (Beginner)

| Field | Value |
|-------|-------|
| **Scenario** | Horizontal beam supported at both ends, loaded from above (simply supported beam). Common for shelving crossbars, bridging elements. |
| **Fixed supports** | Both end faces. Use Surface mode on each end face. |
| **Force** | Downward force on the top face. Use Surface mode. Estimate total weight the beam must carry and apply as a single force. |
| **Torque** | None. |
| **Key insight** | A simply supported beam has maximum stress at the center (bottom face in tension, top face in compression). The FEA will show this classic bending pattern. Supports on both ends prevent the beam from acting as a cantilever. |
| **Recommended settings** | Material: PLA. Safety Factor: 2.0x. Mesh: Balanced. |
| **Expected stress pattern** | Maximum stress at mid-span, decreasing toward the supports. Bottom face in tension (higher stress), top face in compression. |
| **Difficulty** | Beginner |
| **Category** | Beam |

#### EX05: Clamped Plate with Pressure (Intermediate)

| Field | Value |
|-------|-------|
| **Scenario** | Flat plate clamped around its edges, subjected to uniform pressure on one face. Examples: enclosure lid under internal pressure, window panel, tank wall. |
| **Fixed supports** | All four edges (or the entire perimeter). Select each edge face using Surface mode. |
| **Force** | Uniform force on the large flat face. Calculate: pressure (Pa) x area (m^2) = total force (N). Example: 0.1 bar = 10,000 Pa on a 50x50mm plate = 10,000 x 0.0025 = 25 N total. |
| **Torque** | None. |
| **Key insight** | Thin plates under pressure deflect significantly. If deflection exceeds ~10% of plate thickness, the linear elastic assumption becomes less accurate. Use Fine mesh quality for thin plates to capture the bending gradient through the thickness. |
| **Recommended settings** | Material: PC or PETG. Safety Factor: 2.5x. Mesh: Fine (for thin plates). |
| **Expected stress pattern** | Maximum stress at the center of the plate and at the clamped edges. Zero stress at the neutral plane (mid-thickness). |
| **Difficulty** | Intermediate |
| **Category** | Plate, Enclosure |

#### EX06: Bolt-On Handle / Lever (Beginner)

| Field | Value |
|-------|-------|
| **Scenario** | Handle or lever bolted to a surface at one end, gripped and pulled/pushed at the other. Examples: cabinet handle, tool lever, crank arm. |
| **Fixed supports** | Bolt holes at the attachment end. Use Fix Bolt Holes quick setup, or manually use Hole mode to select each bolt hole's inner wall. |
| **Force** | Grip force at the handle's free end. A firm pull: 50-100 N. Direction: perpendicular to the handle axis (for a pull) or along the handle (for a twist). |
| **Torque** | If the handle is twisted (like turning a valve), apply torque at the grip area. Use Surface mode on the grip face. Axis: along the handle. Typical hand twist: 2-10 Nm. |
| **Key insight** | The highest stress is at the base of the handle near the bolt holes, not at the grip. Design the thickest section there. If using bolt holes as supports, ensure the hole diameter setting matches your actual screws. |
| **Recommended settings** | Material: PETG or Nylon (for some flexibility). Safety Factor: 2.0x. Mesh: Balanced. |
| **Expected stress pattern** | Stress concentrated at the junction between the handle shaft and the mounting flange. Secondary stress around bolt holes. |
| **Difficulty** | Beginner |
| **Category** | Handle, Lever |

### 6.4 SVG Diagram Requirements

Each example needs a dedicated SVG diagram showing:

1. **The part geometry** in a simplified isometric view (similar to existing `guide_support.svg` style)
2. **Color-coded BC zones:**
   - Green (#22AA44) with hatching: fixed support faces
   - Red (#DD4444) with arrows: force application faces and direction
   - Blue (#4488DD) with curved arrows: torque application faces and axis
3. **Text labels** for each BC zone (e.g., "Fixed", "F = 50 N", "T = 5 Nm")
4. **Dimensions or scale** indicators where helpful

SVG files should follow the same conventions as the existing guide icons:
- 200x120px viewBox (or 300x180px for more complex examples)
- Dark background-compatible colors (light text, medium-tone geometry)
- Sans-serif labels at 11-13px

**File location:** `resources/icons/examples/`

```
resources/icons/examples/
  ex01_shelf_bracket.svg
  ex02_robot_arm_bracket.svg
  ex03_motor_mount.svg
  ex04_supported_beam.svg
  ex05_clamped_plate.svg
  ex06_handle_lever.svg
```

### 6.5 Content Storage

Examples are stored in the same `help_content.json` file under a separate top-level key:

```json
{
  "version": 1,
  "entries": { ... },
  "examples": {
    "EX01": {
      "title": "Wall-Mount Shelf Bracket",
      "subtitle": "Cantilever with gravity load",
      "category": ["Bracket"],
      "difficulty": "beginner",
      "image": "../icons/examples/ex01_shelf_bracket.svg",
      "image_alt": "L-shaped bracket with back plate highlighted green (fixed) and top shelf surface highlighted red with downward arrows (force).",
      "scenario": "L-shaped bracket screwed to a wall, supporting a shelf...",
      "supports": {
        "description": "Back plate (the face against the wall, where screws go).",
        "selection_mode": "Surface",
        "instructions": "Click the flat back face. It should highlight entirely."
      },
      "forces": [
        {
          "description": "Downward load from shelf contents.",
          "magnitude_N": 50,
          "direction": "Downward (-Y)",
          "selection_mode": "Surface",
          "face": "Top shelf-bearing surface",
          "estimation": "Shelf load 5 kg x 10 N/kg = 50 N"
        }
      ],
      "torques": [],
      "key_insights": [
        "Highest stress at the inner corner of the L-bend.",
        "If the bracket fails, it will fail at the bend."
      ],
      "expected_stress": "Stress concentration at the inner corner of the L-bend. Low stress on the flat plates.",
      "recommended_settings": {
        "material": "PLA or PETG",
        "safety_factor": 2.0,
        "mesh_quality": "Balanced",
        "infill_pattern": "Gyroid"
      }
    }
  }
}
```

### 6.6 Search and Filtering

**Search:** Matches against title, subtitle, scenario text, and category tags. Simple substring matching is sufficient for 6-10 examples.

**Filter categories:**
- All (default)
- Bracket
- Beam
- Enclosure / Housing
- Handle / Lever
- Robotics

**Difficulty filter:** Beginner / Intermediate / Advanced (toggleable badges, not a dropdown)

### 6.7 Future: "Apply This Setup" Button

In later phases, an "Apply This Setup" button could auto-populate boundary conditions based on the example. This requires:

1. The user selects the relevant model in the viewport
2. The plugin enters a guided mode: "Click the face that corresponds to [green highlighted zone in the diagram]"
3. After each face selection, the BC is auto-configured (force magnitude, direction, etc.)

This is a significant feature beyond the help system and is deferred to a future milestone. For now, examples serve as visual recipes the user follows manually.

---

## 7. Implementation Sketch

### 7.1 Content Storage: JSON File

Help content is stored in a single JSON file rather than Markdown files (unlike SettingsGuide2). Rationale:
- Fewer than 50 help entries (SettingsGuide2 has hundreds of full articles)
- No need for full Markdown rendering -- short text with optional bold/italic
- JSON is directly loadable in QML without a Python parser
- Single file simplifies i18n via Cura's existing `i18n` catalog

**File:** `resources/help/help_content.json`

```json
{
  "version": 1,
  "entries": {
    "D01": {
      "title": "Fixed Supports",
      "tooltip": "Faces that cannot move during analysis. Click to fix mounting points, screws, or resting surfaces.",
      "guide": {
        "image": "../icons/guide_support.svg",
        "image_alt": "Diagram showing an isometric block with its bottom face highlighted in green, fixed to the ground with hatching lines and anchor triangles.",
        "body": "Fixed supports prevent selected faces from moving in any direction during the simulation. They represent where your part is physically held, screwed down, or resting on a surface.\n\nAt least one support is required before running analysis. Supports and forces must be on different faces.",
        "steps": [
          "Click faces where the part is held or mounted",
          "Each click adds a face to the support group",
          "Alt+click (Option on Mac) to deselect a face",
          "Use the Selection helper toolbar for multi-triangle surfaces"
        ],
        "tips": [
          "Use 'Surface' mode to select an entire flat face with one click",
          "Use 'Hole' mode to quickly select bolt hole interiors",
          "At minimum, fix one face. For stability, fix at least 3 non-coplanar points."
        ]
      },
      "related": ["D02", "D07", "G02"]
    }
  }
}
```

### 7.2 QML Component Architecture

```
resources/qml/
  BoundaryConditionPanel.qml      (existing -- modified to add "?" icons)
  help/
    HelpTooltipIcon.qml           (reusable "?" icon with tooltip)
    HelpPopover.qml               (reusable popover card)
    HelpContent.qml               (loads and caches help_content.json)
    OnboardingWizard.qml           (first-run wizard)
```

#### HelpTooltipIcon.qml (Atom)

```qml
// Reusable "?" icon that shows a tooltip on hover and optionally
// opens a popover on click (if guideId is set).

import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM

Item {
    id: helpIcon

    property string tooltipText: ""    // Short tooltip (shown on hover)
    property string guideId: ""        // Entry ID in help_content.json (click opens popover)
    property var helpContent: null     // Reference to HelpContent singleton

    width: 20; height: 20             // 20px icon with 24px hit area

    UM.ColorImage {
        anchors.centerIn: parent
        source: UM.Theme.getIcon("Help")  // or a custom "?" icon
        color: UM.Theme.getColor("text_inactive")
        width: 16; height: 16
    }

    MouseArea {
        anchors.fill: parent
        anchors.margins: -2            // Expand hit area to 24x24
        hoverEnabled: true
        cursorShape: Qt.WhatsThisCursor

        onClicked: {
            if (guideId !== "" && helpContent) {
                helpContent.openPopover(guideId, helpIcon)
            }
        }
    }

    ToolTip {
        visible: parent.children[1].containsMouse && tooltipText !== ""
        text: tooltipText
        delay: 500
        timeout: 10000
        width: Math.min(implicitWidth, 240)
    }

    Accessible.role: Accessible.Button
    Accessible.name: tooltipText !== "" ? "Help: " + tooltipText : "Help"
}
```

#### HelpPopover.qml (Molecule)

```qml
// Popover card displaying a help guide entry.
// Positioned relative to the triggering element.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM

Popup {
    id: helpPopover

    property string title: ""
    property string imagePath: ""
    property string imageAlt: ""
    property string body: ""
    property var steps: []           // list of strings
    property var tips: []            // list of strings

    width: 260
    height: Math.min(contentColumn.implicitHeight + 32, 400)
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: UM.Theme.getSize("default_margin").width

    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
        // Drop shadow via layer effect (platform-dependent)
    }

    Accessible.role: Accessible.Dialog
    Accessible.name: title

    ScrollView {
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            id: contentColumn
            width: helpPopover.width - helpPopover.padding * 2
            spacing: UM.Theme.getSize("default_margin").height / 2

            // Header row
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: helpPopover.title
                    font: UM.Theme.getFont("medium_bold")
                    Layout.fillWidth: true
                }
                UM.ColorImage {
                    source: UM.Theme.getIcon("Cancel")
                    color: UM.Theme.getColor("text_medium")
                    width: 20; height: 20
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -4  // 28px hit area
                        onClicked: helpPopover.close()
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: "Close help"
                }
            }

            // SVG illustration
            Image {
                visible: helpPopover.imagePath !== ""
                source: helpPopover.imagePath !== ""
                    ? Qt.resolvedUrl(helpPopover.imagePath) : ""
                Layout.fillWidth: true
                Layout.preferredHeight: 100
                fillMode: Image.PreserveAspectFit
                sourceSize.width: width
                Accessible.role: Accessible.Graphic
                Accessible.description: helpPopover.imageAlt
            }

            // Body text
            UM.Label {
                visible: helpPopover.body !== ""
                text: helpPopover.body
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            // Numbered steps
            ColumnLayout {
                visible: helpPopover.steps.length > 0
                Layout.fillWidth: true
                spacing: 2

                UM.Label {
                    text: "Steps:"
                    font: UM.Theme.getFont("small_bold")
                }

                Repeater {
                    model: helpPopover.steps
                    UM.Label {
                        text: (index + 1) + ". " + modelData
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.leftMargin:
                            UM.Theme.getSize("default_margin").width / 2
                    }
                }
            }

            // Tips list
            ColumnLayout {
                visible: helpPopover.tips.length > 0
                Layout.fillWidth: true
                spacing: 2

                UM.Label {
                    text: "Tips:"
                    font: UM.Theme.getFont("small_bold")
                }

                Repeater {
                    model: helpPopover.tips
                    UM.Label {
                        // Unicode bullet
                        text: "\u2022 " + modelData
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_medium")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.leftMargin:
                            UM.Theme.getSize("default_margin").width / 2
                    }
                }
            }
        }
    }
}
```

#### HelpContent.qml (Singleton/Service)

```qml
// Loads help_content.json and manages the popover lifecycle.
// Instantiated once in BoundaryConditionPanel.qml.

import QtQuick 2.15

QtObject {
    id: helpContentManager

    property var entries: ({})
    property bool loaded: false

    // Active popover reference
    property var _popover: null

    Component.onCompleted: {
        // Load JSON (synchronous for small file)
        var xhr = new XMLHttpRequest()
        xhr.open("GET", Qt.resolvedUrl("../help/help_content.json"), false)
        xhr.send()
        if (xhr.status === 200 || xhr.status === 0) {
            var data = JSON.parse(xhr.responseText)
            entries = data.entries || {}
            loaded = true
        }
    }

    function getTooltip(entryId) {
        if (!loaded || !(entryId in entries)) return ""
        return entries[entryId].tooltip || ""
    }

    function openPopover(entryId, anchor) {
        if (!loaded || !(entryId in entries)) return
        var entry = entries[entryId]
        var guide = entry.guide || {}

        // Close existing popover if open
        if (_popover) _popover.close()

        // Create and show popover
        // (In practice, use a pre-existing Popup component and update its
        //  properties rather than creating new QML objects dynamically)
    }
}
```

#### OnboardingWizard.qml (Organism)

```qml
// Modal overlay shown on first activation of the FEA tool.
// Controlled by a Cura preference: fea_optimizer/onboarding_completed

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Popup {
    id: onboarding

    property int currentStep: 0
    property int totalSteps: 4

    modal: true
    width: 300; height: 380
    anchors.centerIn: Overlay.overlay
    closePolicy: Popup.CloseOnEscape

    // Step definitions (title, optional image, body text)
    property var steps: [
        {
            title: "What does this tool do?",
            image: "",
            body: "This tool analyzes structural loads on your 3D model..."
        },
        {
            title: "Step 1: Define Supports",
            image: Qt.resolvedUrl("../icons/guide_support.svg"),
            body: "Click faces where your part is held, mounted, or resting..."
        },
        // ... steps 3 and 4
    ]

    // ... (QML layout with step content, dot indicators, Next/Skip/Done)

    Accessible.role: Accessible.Dialog
    Accessible.name: "Getting Started, Step " + (currentStep + 1)
        + " of " + totalSteps
}
```

### 7.3 Integration with BoundaryConditionPanel.qml

The existing panel file requires minimal changes:

1. **Import the help components** at the top of the file.
2. **Instantiate `HelpContent`** as a singleton at the root level.
3. **Add `HelpTooltipIcon`** inline after specific labels using `RowLayout`.
4. **Add `HelpPopover`** as a single child item (reused for all popover content).
5. **Add `OnboardingWizard`** with a preference check.

Example modification for the Safety Factor label (line 1403):

```qml
// Before:
UM.Label {
    text: catalog.i18nc("@label", "Safety Factor")
    font: UM.Theme.getFont("medium_bold")
}

// After:
RowLayout {
    Layout.fillWidth: true
    spacing: 4

    UM.Label {
        text: catalog.i18nc("@label", "Safety Factor")
        font: UM.Theme.getFont("medium_bold")
        Layout.fillWidth: true
    }

    HelpTooltipIcon {
        tooltipText: helpContent.getTooltip("O12")
        guideId: "O12"
        helpContent: helpContentManager
    }
}
```

### 7.4 Python Backend (Minimal)

No Python backend is needed for the help system. All content is loaded from JSON directly in QML. The onboarding preference is managed via Cura's existing `UM.Preferences` API:

```python
# In the tool's __init__ or registration:
preferences = CuraApplication.getInstance().getPreferences()
preferences.addPreference("fea_optimizer/onboarding_completed", False)
```

This is significantly lighter than SettingsGuide2's Python backend (which parses Markdown, manages article locations, handles translations, and modifies tooltips). The FEA plugin's help system is self-contained in QML.

### 7.5 Examples Gallery Dialog (QML Sketch)

The Examples Gallery is a standalone QML window created via `CuraApplication.createQmlComponent()`, similar to how SettingsGuide2 creates its window.

```qml
// ExamplesGallery.qml -- Browsable use-case library

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog {
    id: examplesDialog
    title: "Example Setups -- FEA Infill Optimizer"
    width: 700 * screenScaleFactor
    height: 500 * screenScaleFactor
    minimumWidth: 500 * screenScaleFactor
    minimumHeight: 350 * screenScaleFactor

    property var examples: []          // Loaded from help_content.json
    property string searchQuery: ""
    property string selectedCategory: "all"
    property string selectedExampleId: ""  // Empty = gallery view, non-empty = detail view

    UM.I18nCatalog { id: catalog; name: "cura" }

    // --- Gallery View ---
    Item {
        anchors.fill: parent
        visible: selectedExampleId === ""

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            // Search + filter bar
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                TextField {
                    Layout.fillWidth: true
                    placeholderText: catalog.i18nc("@placeholder", "Search examples...")
                    onTextChanged: examplesDialog.searchQuery = text
                }

                ComboBox {
                    model: ["All", "Bracket", "Beam", "Enclosure", "Handle"]
                    onCurrentTextChanged:
                        examplesDialog.selectedCategory = currentText.toLowerCase()
                }
            }

            // Card grid
            GridView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                cellWidth: (width - UM.Theme.getSize("default_margin").width) / 2
                cellHeight: 200 * screenScaleFactor
                clip: true

                model: filteredExamples  // JS-filtered from examples array

                delegate: Rectangle {
                    width: GridView.view.cellWidth - 8
                    height: GridView.view.cellHeight - 8
                    color: UM.Theme.getColor("main_background")
                    border.color: cardMouse.containsMouse
                        ? UM.Theme.getColor("primary")
                        : UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: 4

                        Image {
                            source: modelData.image
                            Layout.fillWidth: true
                            Layout.preferredHeight: 100
                            fillMode: Image.PreserveAspectFit
                            sourceSize.width: width
                            Accessible.description: modelData.image_alt
                        }
                        UM.Label {
                            text: modelData.title
                            font: UM.Theme.getFont("medium_bold")
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        UM.Label {
                            text: modelData.subtitle
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_medium")
                            Layout.fillWidth: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Rectangle {
                                width: diffLabel.implicitWidth + 12
                                height: diffLabel.implicitHeight + 4
                                radius: 4
                                color: modelData.difficulty === "beginner"
                                    ? "#1a7a3a15" : modelData.difficulty === "intermediate"
                                    ? "#1a7a6a15" : "#1a3a7a15"
                                UM.Label {
                                    id: diffLabel
                                    anchors.centerIn: parent
                                    text: modelData.difficulty
                                    font: UM.Theme.getFont("small")
                                }
                            }
                            Item { Layout.fillWidth: true }
                            UM.Label {
                                text: catalog.i18nc("@action", "View Setup >")
                                color: UM.Theme.getColor("primary")
                                font: UM.Theme.getFont("small")
                            }
                        }
                    }

                    MouseArea {
                        id: cardMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: examplesDialog.selectedExampleId = modelData.id
                    }

                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.title + ", " + modelData.difficulty
                }
            }
        }
    }

    // --- Detail View ---
    // (Shown when selectedExampleId !== "")
    // Contains: Back button, large SVG, scenario text, BC cards,
    // key insights, expected results, recommended settings.
    // Structured similarly to the detail view wireframe in Section 6.2.
}
```

**File location:** `resources/qml/help/ExamplesGallery.qml`

The dialog is opened from the BoundaryConditionPanel via a controller property:

```qml
// In BoundaryConditionPanel.qml:
Cura.SecondaryButton {
    text: catalog.i18nc("@action:button", "View Examples")
    onClicked: UM.Controller.setProperty("OpenExamplesGallery", true)
}
```

The Python tool class creates the QML window on demand:

```python
def _open_examples_gallery(self):
    if self._examples_dialog is None:
        path = os.path.join(self._plugin_path, "resources", "qml",
                            "help", "ExamplesGallery.qml")
        self._examples_dialog = CuraApplication.getInstance() \
            .createQmlComponent(path, {"manager": self})
    if self._examples_dialog:
        self._examples_dialog.show()
```

### 7.6 Internationalization (i18n)

All user-visible strings in `help_content.json` should use Cura's `i18n` catalog. Two approaches:

**Option A (Recommended for MVP):** Store English-only strings in JSON. Wrap them in `catalog.i18nc()` calls when displayed in QML. Translators work with the QML files, not the JSON.

**Option B (Future):** Create language-specific JSON files (`help_content_en.json`, `help_content_de.json`, etc.) loaded based on Cura's language preference. More work, better for community translations.

---

## 8. Phased Rollout

### Phase 1: MVP (Immediate Value)

**Scope:** Tooltips + enhanced inline guidance. Estimated effort: 1-2 days.

**Deliverables:**
1. `HelpTooltipIcon.qml` component
2. Add "?" tooltip icons to the 15 most important labels:
   - DEFINE: Supports tab, Forces tab, Torques tab, Selection helper, Quick Setup buttons (3)
   - OPTIMIZE: Material, Infill Pattern, Safety Factor, Mesh Quality, Advanced Settings header
   - REVIEW: Verdict chip, Max Stress, Safety Factor result, Stress Map toggle
3. Tooltip text stored as inline strings (no JSON file yet)
4. Enhance the existing step guide (line 106-111) with better formatting

**Why this first:** Tooltips provide the highest help-value-to-effort ratio. They require no new UI paradigms, no content management system, and no popover infrastructure. Every user benefits immediately.

**Quality gate:** Every tooltip must meet WCAG 2.1 AA contrast requirements. Every "?" icon must be keyboard-focusable and screen-reader-labeled.

### Phase 2: Guide Popovers (Rich Content)

**Scope:** Popover cards with SVG diagrams and step-by-step guides. Estimated effort: 2-3 days.

**Deliverables:**
1. `HelpPopover.qml` component
2. `help_content.json` with all entries from the content inventory
3. `HelpContent.qml` loader/manager
4. Guide popovers for the three BC tabs (supports, forces, torques) using existing SVG icons
5. Material comparison popover (table of all 7 materials)
6. Infill pattern comparison popover (table with exponents and recommendations)
7. Verdict explanation popover
8. ERROR phase expandable troubleshooting section (3-5 entries)

**Why this second:** Popovers address the hobbyist user's need for deeper explanations. The SVG guide icons already exist, making the BC tab guides high-impact and low-effort.

### Phase 3: Onboarding and Polish

**Scope:** First-run wizard, remaining content, cross-references. Estimated effort: 2-3 days.

**Deliverables:**
1. `OnboardingWizard.qml` with 4-step walkthrough
2. Preference tracking (`fea_optimizer/onboarding_completed`)
3. "Related topics" links in popovers (linking entries via `related` field)
4. Advanced concept explanations (SIMP OC, Tsai-Hill, homogenization exponents)
5. "Was this helpful?" feedback mechanism (stores preference, no telemetry)
6. i18n preparation: Extract all strings to `catalog.i18nc()` calls
7. Additional SVG diagrams: stress map color scale legend, mesh quality comparison

**Why this third:** The onboarding wizard is valuable for first-time users but does not help returning users. Advanced concept explanations are important but serve a smaller audience. Polish items like "related topics" and feedback add refinement.

### Phase 4: Use Case Library / Examples Gallery

**Scope:** Browsable gallery of real-world example setups. Estimated effort: 3-5 days.

**Deliverables:**
1. `ExamplesGallery.qml` dialog with card grid and detail views
2. "View Examples" button in the DEFINE phase step guide
3. "Examples" link in the panel header (accessible from any phase)
4. 6 initial example entries (EX01-EX06 from Section 6.3):
   - Wall-Mount Shelf Bracket (Beginner)
   - Robot Arm Joint Bracket (Intermediate)
   - Gear Housing / Motor Mount (Intermediate)
   - Structural Beam with Distributed Load (Beginner)
   - Clamped Plate with Pressure (Intermediate)
   - Bolt-On Handle / Lever (Beginner)
5. 6 SVG diagrams (one per example) in `resources/icons/examples/`
6. Search and category filtering in the gallery view
7. Example data added to `help_content.json` under the `"examples"` key

**Why this last:** The examples gallery is the most content-intensive deliverable. Each example requires a custom SVG diagram showing the part with annotated BCs. The gallery dialog is a new window that adds UI surface area. However, it is arguably the single most valuable help feature for hobbyist users who learn best from concrete examples rather than abstract explanations. By placing it in Phase 4, we ensure the foundational help system (tooltips, popovers, onboarding) is solid before investing in the gallery.

**Future extensions (beyond Phase 4):**
- "Apply This Setup" button that enters guided BC application mode
- Community-contributed examples (user uploads their setup as a shareable JSON)
- Before/after comparison: show the stress map and optimized infill for each example
- Animated walkthroughs (step-by-step video or GIF showing the clicks)

---

## 9. Accessibility Considerations

### WCAG 2.1 AA Compliance Checklist

| Requirement | How Addressed |
|-------------|--------------|
| **4.5:1 contrast for body text** | All tooltip and popover text uses `UM.Theme.getColor("text")` on `main_background`. Cura's built-in themes meet AA contrast. Custom colors must be verified. |
| **3:1 contrast for UI components** | The "?" icon uses `text_inactive` color which must be verified against `main_background`. If insufficient, fall back to `text_medium`. |
| **Keyboard accessibility** | "?" icons are focusable via Tab. Popovers trap focus and close on Escape. Onboarding wizard traps focus with Tab cycling. |
| **Screen reader support** | All "?" icons have `Accessible.name`. Popovers use `Accessible.role: Dialog`. SVG images have `Accessible.description` with alt text. |
| **Touch targets >= 44px** | "?" icons have a 24px visual size but the clickable area for opening popovers is via MouseArea with expanded margins. However, the icon itself is a secondary affordance adjacent to a larger label/control. Full popover buttons (Close, Next, Skip) meet 44px minimum. |
| **Color independence** | The verdict chip already uses text labels + background color (not color alone). Tooltips use text only. SVG diagrams use labeled elements. |
| **Motion safety** | Popover open/close: no animation (instant appear/disappear) or a simple opacity fade (150ms, respects `prefers-reduced-motion`). Onboarding step transitions: crossfade only. |
| **Focus indicators** | "?" icons show the standard Cura focus ring (2px outline). Popover elements inherit Cura's focus styling. |

### Screen Reader Experience

When a screen reader user tabs to a "?" icon:

1. **Announcement:** "Help: [tooltip text]. Button."
2. **On activation (Enter/Space):** Popover opens, focus moves to popover title.
3. **In popover:** Tab cycles through title, body text, steps, tips, close button.
4. **On Escape:** Popover closes, focus returns to the "?" icon.

### Keyboard Shortcuts (Future Consideration)

No keyboard shortcuts are proposed for Phase 1-3. If demand arises:
- `F1` while a setting is focused: Open the help popover for that setting
- `?` key: Toggle contextual help mode (all "?" icons highlight)

---

## Appendix A: Comparison to SettingsGuide2

| Aspect | SettingsGuide2 | FEA Help System |
|--------|---------------|-----------------|
| **Scope** | All ~400 Cura settings | ~50 plugin-specific topics |
| **UI** | Separate 1200x640 window with sidebar + article area | In-panel tooltips + popovers (280px width) |
| **Content format** | Markdown files with images, parsed by Python | JSON file with structured entries, loaded in QML |
| **Content volume** | Hundreds of full articles with screenshots | ~50 entries: mostly 1-3 paragraphs each |
| **Python backend** | Heavy (Markdown parser, article manager, tooltip override) | Minimal (preference for onboarding flag only) |
| **Translation** | Per-language Markdown directories | `i18nc()` catalog integration |
| **Images** | Screenshots + diagrams per article | 3 existing SVG guide icons + future additions |
| **Integration** | Context menu item + Extensions menu + tooltip override | Inline "?" icons + first-run wizard |

The SettingsGuide2 architecture is appropriate for its scope (comprehensive reference for hundreds of settings) but would be over-engineered for this plugin. The FEA help system is designed for a narrower, deeper need: making complex engineering concepts accessible within a constrained side panel.

---

## Appendix B: File Manifest

Files to create (not yet existing):

```
plugins/FEAInfillOptimizer/
  resources/
    help/
      help_content.json              # All help entries + examples (Phase 2/4)
    icons/
      examples/
        ex01_shelf_bracket.svg       # Example diagram (Phase 4)
        ex02_robot_arm_bracket.svg   # Example diagram (Phase 4)
        ex03_motor_mount.svg         # Example diagram (Phase 4)
        ex04_supported_beam.svg      # Example diagram (Phase 4)
        ex05_clamped_plate.svg       # Example diagram (Phase 4)
        ex06_handle_lever.svg        # Example diagram (Phase 4)
    qml/
      help/
        HelpTooltipIcon.qml          # Reusable "?" icon (Phase 1)
        HelpPopover.qml              # Popover card (Phase 2)
        HelpContent.qml              # JSON loader/manager (Phase 2)
        OnboardingWizard.qml          # First-run wizard (Phase 3)
        ExamplesGallery.qml           # Use case library dialog (Phase 4)
```

Files to modify:

```
plugins/FEAInfillOptimizer/
  resources/qml/
    BoundaryConditionPanel.qml       # Add "?" icons, "View Examples" button,
                                     #   import help components
  __init__.py (or tool registration) # Add onboarding preference,
                                     #   examples gallery opener
```

---

*End of design document.*

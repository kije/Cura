# Torque Quick Setup: Physics Design Document

## 1. Overview

The Torque Quick Setup feature lets users place a visible axis line in the 3D scene representing a physical rotation axis (motor shaft, hinge pin, bolt axis), select faces connected to that axis, and automatically generate a torque boundary condition. This replaces the current approach where the torque center is derived implicitly from the centroid of selected nodes.

---

## 2. Axis Representation

### 2.1 Mathematical Definition

An axis in 3D is a line defined by:
- **P** — a point on the axis (position, world coordinates)
- **d** — a unit direction vector along the axis

The axis line extends infinitely in both directions: `L(t) = P + t * d`, for all t in (-inf, +inf).

### 2.2 Scene Node Extraction

The axis line node starts at the model center pointing along +Y. The user moves and rotates it. Given the node's world transformation matrix **M** (4x4 homogeneous):

```
P = M * [0, 0, 0, 1]^T    (extract translation = columns 3, or M[:3, 3])
d_raw = M * [0, 1, 0, 0]^T (transform the local +Y direction, no translation)
d = d_raw / ||d_raw||       (normalize to unit vector)
```

In Cura's `SceneNode` API:
```python
world_transform = axis_node.getWorldTransformation()   # 4x4 numpy matrix
P = world_transform[:3, 3]                              # axis origin in world space
d_raw = world_transform[:3, 1]                          # second column = local Y in world
d = d_raw / np.linalg.norm(d_raw)                       # unit direction
```

Note: Cura's transformation matrix is column-major (OpenGL convention), so `world_transform[:3, 1]` extracts the Y-axis basis vector.

### 2.3 Invariants

- `||d|| = 1` always (re-normalize after extraction to avoid floating-point drift from non-uniform scale)
- **P** is an arbitrary point on the infinite line; choosing a different point along the line does not change the physics — only the direction **d** and the line's position in space matter

---

## 3. Core Mathematics

### 3.1 Perpendicular Distance from a Point to an Axis Line

For a node at position **q**, the perpendicular distance to the axis line `L(t) = P + t*d` is:

```
r_vec = q - P
r_parallel = (r_vec . d) * d           # projection onto axis
r_perp_vec = r_vec - r_parallel         # perpendicular component (vector)
r_perp = ||r_perp_vec||                 # perpendicular distance (scalar)
```

Equivalently, using the cross product:

```
r_perp = ||(q - P) x d||
```

Both formulations are mathematically identical. The first form is preferred in implementation because we also need `r_perp_vec` (the perpendicular vector) to compute the tangential direction.

**Current implementation defect:** Line 1008 of `iterative_solver.py` computes `r = pos - center` where `center` is the centroid of selected nodes. This computes distance from a **point** (the centroid), not from a **line** (the axis). This is only correct when the selected nodes are coplanar and perpendicular to the axis — a special case that rarely holds in practice.

### 3.2 Tangential Force Direction

For a rigid body rotating about axis **d**, the tangential (force) direction at each node is perpendicular to both the axis and the radial direction:

```
tangent = d x r_perp_vec / ||d x r_perp_vec||
```

Since `r_perp_vec` is already perpendicular to **d**, this simplifies to:

```
tangent = d x r_perp_vec / r_perp
```

(because `||d x r_perp_vec|| = ||d|| * ||r_perp_vec|| * sin(90deg) = r_perp`)

The sign of the tangent follows the right-hand rule around **d**: if **d** points up, torque is counterclockwise when viewed from above. Reversing **d** or negating `T_mag` reverses the rotation direction.

### 3.3 Rigid-Body Rotation Force Model

**Physical model:** All nodes undergo the same angular displacement `theta` about the axis. For small displacements, the arc displacement at node *i* is `u_i = theta * r_perp_i`. If we model each node as a point load connected by a linear spring of unit stiffness, then:

```
F_i = lambda * r_perp_i
```

where `lambda` is a proportionality constant. The total torque about the axis is:

```
T = sum_i (F_i * r_perp_i) = lambda * sum_i (r_perp_i^2)
```

Solving for lambda:

```
lambda = T / sum_i (r_perp_i^2)
```

Therefore the force magnitude at node *i* is:

```
F_i = T * r_perp_i / sum_i (r_perp_i^2)
```

And the force vector at node *i* is:

```
F_vec_i = F_i * tangent_i
```

### 3.4 Verification: Total Torque Equals T

The moment contribution of node *i* about the axis is:

```
M_i = F_i * r_perp_i = T * r_perp_i^2 / sum_j (r_perp_j^2)
```

Summing over all nodes:

```
sum_i M_i = T * sum_i (r_perp_i^2) / sum_j (r_perp_j^2) = T   [QED]
```

The formulation is self-consistent: the distributed forces always produce exactly the requested torque **T** about the axis.

### 3.5 Net Force Verification

For a pure torque (couple), the net translational force should ideally be zero. With the axis-line model, this holds exactly when the node distribution is symmetric about the axis. For asymmetric distributions (faces on one side only), there will be a residual net force. This is physically correct — an off-center torque application does produce a net force on the body.

---

## 4. Comparison: Centroid Model vs. Axis-Line Model

| Aspect | Current (Centroid) | Proposed (Axis Line) |
|--------|-------------------|---------------------|
| **Center** | Centroid of selected nodes | User-placed axis position P |
| **r_perp computation** | `pos - centroid`, then remove axis component | `pos - P`, then remove axis component |
| **Axis position dependence** | Implicit (moves with face selection) | Explicit (fixed by scene node) |
| **Physical accuracy** | Correct only for symmetric, coplanar face rings | Correct for any face distribution around any axis |
| **Off-axis placement** | Cannot model (centroid always inside selection) | Fully supported (axis can be offset from faces) |
| **Through-body axis** | Approximate (centroid may not lie on physical axis) | Exact (user places axis on physical rotation center) |
| **Net force** | Guaranteed zero (symmetric by construction) | May be nonzero for asymmetric selections (physically correct) |

### 4.1 Key Behavioral Difference

Consider a shaft where the user selects faces on one side only (e.g., a flat face on a D-shaft):

- **Centroid model:** Places the center at the centroid of the flat face. `r_perp` values are small (within the face). The torque is applied as if the rotation center is the face center — physically wrong.
- **Axis-line model:** The axis is at the shaft center. `r_perp` values reflect the true distance from the shaft center. Forces are tangential to the shaft — physically correct.

---

## 5. Data Model Changes

### 5.1 TorqueGroup Enhancement

Current `TorqueGroup` fields:
- `face_indices: List[int]`
- `torque_axis: Vector` (direction only)
- `torque_magnitude: float`

**Add new field:**
- `torque_center: Optional[Vector]` — a point on the axis line (world coordinates)

When `torque_center` is `None`, fall back to the current centroid behavior (backward compatibility). When set, use the axis-line model.

```python
class TorqueGroup:
    def __init__(self, face_indices, torque_axis, torque_magnitude,
                 torque_center=None):
        self.face_indices = list(face_indices)
        self.torque_axis = torque_axis
        self.torque_magnitude = torque_magnitude
        self.torque_center = torque_center  # None = use centroid (legacy)
```

### 5.2 Serialization

```python
def to_dict(self):
    d = {
        "face_indices": self.face_indices,
        "torque_axis": [self.torque_axis.x, self.torque_axis.y, self.torque_axis.z],
        "torque_magnitude": self.torque_magnitude
    }
    if self.torque_center is not None:
        d["torque_center"] = [self.torque_center.x, self.torque_center.y,
                              self.torque_center.z]
    return d
```

Deserialization reads `torque_center` if present, else `None`.

### 5.3 FEABoundaryConditionDecorator Changes

Update `addTorqueGroup()` signature:

```python
def addTorqueGroup(self, face_indices, torque_axis, torque_magnitude,
                   torque_center=None):
    self._torque_groups.append(
        TorqueGroup(face_indices, torque_axis, torque_magnitude, torque_center))
```

Add `updateTorqueCenter()`:

```python
def updateTorqueCenter(self, index: int, new_center: Optional[Vector]) -> None:
    if 0 <= index < len(self._torque_groups):
        self._torque_groups[index].torque_center = new_center
```

---

## 6. Solver Changes (`_build_force_vector`)

### 6.1 Current Code (lines 996-1033 of iterative_solver.py)

```python
# Current: center from node centroid
center = node_positions.mean(axis=0)
...
r = pos - center
```

### 6.2 Proposed Change

```python
# New: use explicit axis center if available, else fall back to centroid
if torque_group.torque_center is not None:
    tc = torque_group.torque_center
    center = np.array([float(tc.x), float(tc.y), float(tc.z)])
else:
    center = node_positions.mean(axis=0)
```

The rest of the algorithm (`r_perp`, tangent, force distribution) remains **unchanged** — the formulas are already correct for an arbitrary center point. The only fix is the source of `center`.

This is a **3-line change** in the solver. The mathematical formulation (rigid-body rotation model, F_i = T * r_perp_i / sum(r_perp_i^2)) is already correctly implemented. The deficiency was only in where the center point came from.

---

## 7. Edge Cases

### 7.1 Node on the Axis (r_perp ~ 0)

A node exactly on the axis has zero perpendicular distance, thus zero tangential force. This is physically correct — a point on the rotation axis doesn't move under pure rotation.

**Handling:** Already implemented. Skip nodes where `r_perp < 1e-12` (line 1025). No change needed.

**Degenerate case:** If ALL nodes are on the axis (`sum_r2 < 1e-24`), the torque cannot be applied. Already handled by `continue` at line 1021. The UI should warn the user.

### 7.2 Axis Through the Model Interior

Common for shafts, holes, and cylindrical features. The axis passes through the solid body, and selected faces surround it. This is the ideal use case — forces are distributed tangentially around the axis, producing a pure torque with zero net force.

No special handling needed. The math works naturally.

### 7.3 Axis Outside the Model

The axis is offset from all selected faces (e.g., a hinge with the pin outside the part). All nodes have nonzero `r_perp`, and the tangential forces will all point roughly the same direction — producing both torque about the axis AND a net translational force.

This is **physically correct**: an off-center torque application does produce a net force. However, the user may not expect this.

**Validation recommendation:** If the closest distance from the axis to any selected node exceeds 5x the bounding radius of the selection, warn the user that the axis appears to be far from the selected faces.

### 7.4 Asymmetric Face Selection (One-Sided)

If faces are selected on only one side of the axis (e.g., a flat face tangent to a cylinder), the resulting forces will be mostly parallel. This produces:
- Correct torque T about the axis
- Nonzero net force (translational component)

This is physically valid but may indicate user error (they meant to select faces all around the axis). No minimum symmetry requirement — the math handles any distribution.

**Validation recommendation:** Compute the angular coverage of selected faces around the axis. If coverage < 90 degrees, suggest selecting faces on the opposite side for better load distribution.

### 7.5 Multiple Disconnected Face Regions

Multiple face clusters (e.g., two separate patches on a gear) are handled naturally. Each node contributes independently based on its `r_perp` from the axis. No special treatment needed.

### 7.6 Very Small Models / Tolerance Issues

For models where characteristic dimensions are < 0.1 mm, the `r_perp < 1e-12` tolerance (in model units, mm) may be too tight or too loose.

**Recommendation:** Keep the current absolute tolerance of 1e-12 mm. This is approximately the diameter of an atom and is appropriate for any 3D-printable geometry.

### 7.7 Axis Perpendicular to All Faces

If the axis is perpendicular to the face normals (e.g., axis along Z but faces are horizontal), the `r_perp` values will be large (equal to the horizontal distances) and the tangential forces will be horizontal. This is physically meaningful (twisting a plate about a vertical axis). No special case needed.

---

## 8. Validation Rules

### 8.1 Minimum Requirements

| Check | Condition | Severity | Action |
|-------|-----------|----------|--------|
| Axis direction | `||d|| > 1e-12` after normalization | Error | Cannot apply torque without axis direction |
| Torque magnitude | `|T_mag| > 1e-12` | Error | Zero torque is a no-op |
| Mapped nodes | At least 1 tet node found | Error | No nodes to apply force to |
| Off-axis nodes | `sum(r_perp_i^2) > 1e-24` | Error | All nodes on axis — cannot apply torque |
| Face count | >= 1 face selected | Error | No faces to distribute torque |

### 8.2 Warnings (Non-Blocking)

| Check | Condition | Warning Message |
|-------|-----------|-----------------|
| Low face count | < 3 faces | "Few faces selected; torque distribution may be coarse" |
| Axis far from faces | min(r_perp) > 5 * selection_radius | "Axis appears far from selected faces" |
| Asymmetric selection | angular coverage < 90 deg | "Faces are clustered on one side of the axis; consider selecting faces around the full circumference" |
| All nodes colinear with axis | all r_perp < 0.1 mm | "Selected faces are nearly on the axis; tangential forces will be very small" |

### 8.3 Ring Detection (Optional Enhancement)

To validate that faces form a "ring" around the axis:

1. Project all face centroids onto the plane perpendicular to **d** at the closest point on the axis
2. Compute the angle of each projected point: `theta_i = atan2(y'_i, x'_i)` in the local axis frame
3. Sort angles, compute gaps between consecutive angles
4. If max gap > 180 degrees, the faces don't form a ring (one-sided selection)

This is an optional UX enhancement, not required for correct physics.

---

## 9. Summary of Required Code Changes

### 9.1 `FEABoundaryConditionDecorator.py`

| Change | Lines | Description |
|--------|-------|-------------|
| Add `torque_center` field to `TorqueGroup.__init__` | 41-45 | New optional `torque_center: Optional[Vector] = None` parameter |
| Update `TorqueGroup.to_dict` | 47-52 | Serialize `torque_center` if not None |
| Update `TorqueGroup.from_dict` | 54-61 | Deserialize `torque_center` if present |
| Update `addTorqueGroup` | 123-125 | Accept optional `torque_center` parameter |
| Add `updateTorqueCenter` | after 134 | New method to update center for a torque group |
| Update `__deepcopy__` | 209-213 | Copy `torque_center` field |

### 9.2 `fea/iterative_solver.py`

| Change | Lines | Description |
|--------|-------|-------------|
| Replace centroid with axis center | 996-998 | Use `torque_group.torque_center` if available, else fall back to `node_positions.mean()` |

This is a 3-line change:
```python
# Line 996-998: Replace
center = node_positions.mean(axis=0)
# With:
if torque_group.torque_center is not None:
    tc = torque_group.torque_center
    center = np.array([float(tc.x), float(tc.y), float(tc.z)])
else:
    center = node_positions.mean(axis=0)
```

### 9.3 No Changes Required

The following are already correct and need no modification:
- Perpendicular distance computation (lines 1008-1011) — already uses `r = pos - center` then removes axis component
- Tangential direction computation (lines 1012-1015)
- Rigid-body force model (lines 1019-1033)
- On-axis node handling (lines 1025-1026)
- Zero sum_r2 handling (lines 1020-1021)

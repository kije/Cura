#!/usr/bin/env python3
"""Generate a test STL file with surfaces suitable for non-planar slicing.

The model contains several features:
1. A gentle dome (top cap is 5-30° from horizontal → non-planar candidate)
2. A ramp at ~20° (clear non-planar candidate)
3. A flat plateau (should NOT be non-planar — 0° angle)
4. A steep wall section (should NOT be non-planar — >30°)
5. A sinusoidal wave surface (mixed angles)

All dimensions in mm. The model sits on Z=0 (build plate).
"""

import struct
import math
import numpy as np
from pathlib import Path


def write_binary_stl(filepath: str, triangles: list[tuple]) -> None:
    """Write triangles to a binary STL file.

    Each triangle is (normal, v0, v1, v2) where each is (x, y, z).
    """
    with open(filepath, "wb") as f:
        # 80-byte header
        f.write(b"\x00" * 80)
        # Number of triangles
        f.write(struct.pack("<I", len(triangles)))
        for normal, v0, v1, v2 in triangles:
            # Normal vector
            f.write(struct.pack("<3f", *normal))
            # Vertices
            f.write(struct.pack("<3f", *v0))
            f.write(struct.pack("<3f", *v1))
            f.write(struct.pack("<3f", *v2))
            # Attribute byte count
            f.write(struct.pack("<H", 0))


def make_dome(cx: float, cy: float, radius: float, height: float,
              n_radial: int = 32, n_rings: int = 16) -> list[tuple]:
    """Create a dome (half-ellipsoid) centered at (cx, cy) on the build plate.

    The dome rises from Z=0 to Z=height with the given XY radius.
    """
    triangles = []

    for i in range(n_rings):
        # Parametric angle from top (0) to equator (pi/2)
        theta0 = (math.pi / 2) * i / n_rings
        theta1 = (math.pi / 2) * (i + 1) / n_rings

        r0 = radius * math.sin(theta0)
        z0 = height * math.cos(theta0)
        r1 = radius * math.sin(theta1)
        z1 = height * math.cos(theta1)

        for j in range(n_radial):
            phi0 = 2 * math.pi * j / n_radial
            phi1 = 2 * math.pi * (j + 1) / n_radial

            # Four corners of the quad
            p00 = (cx + r0 * math.cos(phi0), cy + r0 * math.sin(phi0), z0)
            p01 = (cx + r0 * math.cos(phi1), cy + r0 * math.sin(phi1), z0)
            p10 = (cx + r1 * math.cos(phi0), cy + r1 * math.sin(phi0), z1)
            p11 = (cx + r1 * math.cos(phi1), cy + r1 * math.sin(phi1), z1)

            # Two triangles per quad
            n1 = _compute_normal(p00, p10, p11)
            triangles.append((n1, p00, p10, p11))
            n2 = _compute_normal(p00, p11, p01)
            triangles.append((n2, p00, p11, p01))

    # Bottom cap (flat circle at Z=0)
    for j in range(n_radial):
        phi0 = 2 * math.pi * j / n_radial
        phi1 = 2 * math.pi * (j + 1) / n_radial
        r = radius
        p0 = (cx, cy, 0.0)
        p1 = (cx + r * math.cos(phi1), cy + r * math.sin(phi1), 0.0)
        p2 = (cx + r * math.cos(phi0), cy + r * math.sin(phi0), 0.0)
        triangles.append(((0, 0, -1), p0, p1, p2))

    return triangles


def make_ramp(x0: float, y0: float, width: float, depth: float,
              angle_deg: float) -> list[tuple]:
    """Create a ramp surface rising at the given angle from horizontal.

    Starts at (x0, y0, 0) and rises in the +Y direction.
    """
    height = depth * math.tan(math.radians(angle_deg))

    # Ramp surface (2 triangles)
    bl = (x0, y0, 0.0)
    br = (x0 + width, y0, 0.0)
    tl = (x0, y0 + depth, height)
    tr = (x0 + width, y0 + depth, height)

    triangles = []

    # Top surface (the ramp)
    n1 = _compute_normal(bl, br, tr)
    triangles.append((n1, bl, br, tr))
    n2 = _compute_normal(bl, tr, tl)
    triangles.append((n2, bl, tr, tl))

    # Bottom face
    n = (0, 0, -1)
    triangles.append((n, bl, tl, tr))
    triangles.append((n, bl, tr, br))

    # Side walls
    # Left wall
    n = _compute_normal(bl, tl, (x0, y0 + depth, 0.0))
    triangles.append((n, bl, tl, (x0, y0 + depth, 0.0)))

    # Right wall
    n = _compute_normal(br, (x0 + width, y0 + depth, 0.0), tr)
    triangles.append((n, br, (x0 + width, y0 + depth, 0.0), tr))

    # Back wall (high end)
    n = _compute_normal(tl, tr, (x0 + width, y0 + depth, 0.0))
    triangles.append((n, tl, tr, (x0 + width, y0 + depth, 0.0)))
    triangles.append((n, tl, (x0 + width, y0 + depth, 0.0), (x0, y0 + depth, 0.0)))

    # Front wall (low end)
    n = _compute_normal(bl, (x0 + width, y0, 0.0), bl)
    triangles.append(((0, -1, 0), bl, br, br))  # degenerate, skip

    return triangles


def make_wave_surface(x0: float, y0: float, width: float, depth: float,
                      amplitude: float, base_height: float,
                      n_x: int = 40, n_y: int = 40) -> list[tuple]:
    """Create a sinusoidal wave surface.

    Z = base_height + amplitude * sin(2*pi*x/width) * sin(2*pi*y/depth)

    This creates regions with varying angles — some will be non-planar
    candidates, others won't.
    """
    triangles = []
    dx = width / n_x
    dy = depth / n_y

    def z_func(x, y):
        return base_height + amplitude * math.sin(
            2 * math.pi * (x - x0) / width
        ) * math.sin(
            2 * math.pi * (y - y0) / depth
        )

    for i in range(n_x):
        for j in range(n_y):
            x = x0 + i * dx
            y = y0 + j * dy

            # Four corners
            p00 = (x, y, z_func(x, y))
            p10 = (x + dx, y, z_func(x + dx, y))
            p01 = (x, y + dy, z_func(x, y + dy))
            p11 = (x + dx, y + dy, z_func(x + dx, y + dy))

            # Two triangles
            n1 = _compute_normal(p00, p10, p11)
            triangles.append((n1, p00, p10, p11))
            n2 = _compute_normal(p00, p11, p01)
            triangles.append((n2, p00, p11, p01))

    # Add bottom face
    z_bottom = 0.0
    bl = (x0, y0, z_bottom)
    br = (x0 + width, y0, z_bottom)
    tl = (x0, y0 + depth, z_bottom)
    tr = (x0 + width, y0 + depth, z_bottom)
    triangles.append(((0, 0, -1), bl, tl, tr))
    triangles.append(((0, 0, -1), bl, tr, br))

    # Add side walls (simplified — connect bottom edges to surface edges)
    for i in range(n_x):
        x = x0 + i * dx
        x1 = x + dx
        # Front wall (y = y0)
        p0_top = (x, y0, z_func(x, y0))
        p1_top = (x1, y0, z_func(x1, y0))
        p0_bot = (x, y0, z_bottom)
        p1_bot = (x1, y0, z_bottom)
        n = _compute_normal(p0_bot, p1_bot, p1_top)
        triangles.append((n, p0_bot, p1_bot, p1_top))
        triangles.append((n, p0_bot, p1_top, p0_top))
        # Back wall (y = y0 + depth)
        yb = y0 + depth
        p0_top = (x, yb, z_func(x, yb))
        p1_top = (x1, yb, z_func(x1, yb))
        p0_bot = (x, yb, z_bottom)
        p1_bot = (x1, yb, z_bottom)
        n = _compute_normal(p0_bot, p1_top, p1_bot)
        triangles.append((n, p0_bot, p1_top, p1_bot))
        triangles.append((n, p0_bot, p0_top, p1_top))

    for j in range(n_y):
        y = y0 + j * dy
        y1 = y + dy
        # Left wall (x = x0)
        p0_top = (x0, y, z_func(x0, y))
        p1_top = (x0, y1, z_func(x0, y1))
        p0_bot = (x0, y, z_bottom)
        p1_bot = (x0, y1, z_bottom)
        n = _compute_normal(p0_bot, p1_top, p1_bot)
        triangles.append((n, p0_bot, p1_top, p1_bot))
        triangles.append((n, p0_bot, p0_top, p1_top))
        # Right wall (x = x0 + width)
        xr = x0 + width
        p0_top = (xr, y, z_func(xr, y))
        p1_top = (xr, y1, z_func(xr, y1))
        p0_bot = (xr, y, z_bottom)
        p1_bot = (xr, y1, z_bottom)
        n = _compute_normal(p0_bot, p1_bot, p1_top)
        triangles.append((n, p0_bot, p1_bot, p1_top))
        triangles.append((n, p0_bot, p1_top, p0_top))

    return triangles


def make_box(x0, y0, z0, width, depth, height) -> list[tuple]:
    """Create a simple box (for the flat plateau)."""
    x1, y1, z1 = x0 + width, y0 + depth, z0 + height
    triangles = []

    # Top (+Z)
    triangles.append(((0,0,1), (x0,y0,z1), (x1,y0,z1), (x1,y1,z1)))
    triangles.append(((0,0,1), (x0,y0,z1), (x1,y1,z1), (x0,y1,z1)))
    # Bottom (-Z)
    triangles.append(((0,0,-1), (x0,y0,z0), (x0,y1,z0), (x1,y1,z0)))
    triangles.append(((0,0,-1), (x0,y0,z0), (x1,y1,z0), (x1,y0,z0)))
    # Front (-Y)
    triangles.append(((0,-1,0), (x0,y0,z0), (x1,y0,z0), (x1,y0,z1)))
    triangles.append(((0,-1,0), (x0,y0,z0), (x1,y0,z1), (x0,y0,z1)))
    # Back (+Y)
    triangles.append(((0,1,0), (x0,y1,z0), (x0,y1,z1), (x1,y1,z1)))
    triangles.append(((0,1,0), (x0,y1,z0), (x1,y1,z1), (x1,y1,z0)))
    # Left (-X)
    triangles.append(((-1,0,0), (x0,y0,z0), (x0,y0,z1), (x0,y1,z1)))
    triangles.append(((-1,0,0), (x0,y0,z0), (x0,y1,z1), (x0,y1,z0)))
    # Right (+X)
    triangles.append(((1,0,0), (x1,y0,z0), (x1,y1,z0), (x1,y1,z1)))
    triangles.append(((1,0,0), (x1,y0,z0), (x1,y1,z1), (x1,y0,z1)))

    return triangles


def _compute_normal(p0, p1, p2):
    """Compute the unit normal for triangle (p0, p1, p2)."""
    v0 = np.array(p0, dtype=np.float64)
    v1 = np.array(p1, dtype=np.float64)
    v2 = np.array(p2, dtype=np.float64)
    edge1 = v1 - v0
    edge2 = v2 - v0
    cross = np.cross(edge1, edge2)
    mag = np.linalg.norm(cross)
    if mag < 1e-12:
        return (0.0, 0.0, 1.0)
    n = cross / mag
    return tuple(n)


def main():
    all_triangles = []

    # === Feature 1: Dome (radius 30mm, height 15mm) ===
    # The top cap (0-30° from vertical) is a non-planar candidate.
    # At 30mm radius, the candidate band area ≈ 0.13 * pi * 30^2 ≈ 368 mm²
    # (well above 100mm² threshold)
    print("Generating dome...")
    dome = make_dome(cx=0, cy=0, radius=30, height=15, n_radial=48, n_rings=24)
    all_triangles.extend(dome)
    print(f"  {len(dome)} triangles")

    # === Feature 2: Gentle ramp at 20° (clear candidate) ===
    print("Generating 20° ramp...")
    ramp = make_ramp(x0=40, y0=-20, width=30, depth=40, angle_deg=20)
    all_triangles.extend(ramp)
    print(f"  {len(ramp)} triangles")

    # === Feature 3: Flat plateau (should NOT trigger — 0° angle) ===
    print("Generating flat plateau...")
    plateau = make_box(x0=-70, y0=-15, z0=0, width=30, depth=30, height=10)
    all_triangles.extend(plateau)
    print(f"  {len(plateau)} triangles")

    # === Feature 4: Sinusoidal wave surface (mixed angles) ===
    # amplitude=4mm over 60mm → max slope ≈ atan(4*2pi/60) ≈ 22.6°
    # Some regions will be candidates, others won't
    print("Generating wave surface...")
    wave = make_wave_surface(
        x0=-60, y0=40, width=60, depth=40,
        amplitude=4, base_height=10,
        n_x=50, n_y=30,
    )
    all_triangles.extend(wave)
    print(f"  {len(wave)} triangles")

    # === Feature 5: Steep ramp at 50° (should NOT trigger) ===
    print("Generating 50° steep ramp...")
    steep = make_ramp(x0=40, y0=30, width=30, depth=20, angle_deg=50)
    all_triangles.extend(steep)
    print(f"  {len(steep)} triangles")

    # Filter out degenerate triangles
    valid = []
    for tri in all_triangles:
        n, v0, v1, v2 = tri
        e1 = np.array(v1) - np.array(v0)
        e2 = np.array(v2) - np.array(v0)
        if np.linalg.norm(np.cross(e1, e2)) > 1e-9:
            valid.append(tri)

    print(f"\nTotal: {len(valid)} triangles (filtered {len(all_triangles) - len(valid)} degenerate)")

    # Write STL
    output_path = Path(__file__).parent.parent / "test_models" / "non_planar_test.stl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_binary_stl(str(output_path), valid)
    print(f"Written to: {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024:.1f} KB")

    # Print expected behavior
    print("\n--- Expected Non-Planar Behavior ---")
    print("Feature 1 (Dome):       ✓ Top cap should be GREEN (5-30° band)")
    print("Feature 2 (20° Ramp):   ✓ Entire surface should be GREEN")
    print("Feature 3 (Flat Box):   ✗ Should NOT be candidate (0° = too flat)")
    print("Feature 4 (Wave):       ~ Partial — peaks/troughs GREEN, transitions YELLOW/RED")
    print("Feature 5 (50° Ramp):   ✗ Should NOT be candidate (too steep)")


if __name__ == "__main__":
    main()

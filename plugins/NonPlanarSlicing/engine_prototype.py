#!/usr/bin/env python3
"""Non-Planar Slicing CuraEngine plugin prototype.

A gRPC server that implements the GCODE_PATHS_MODIFY slot (103) for CuraEngine.
Applies inverse deformation to path Z coordinates, restoring curved surfaces
from the deformed (flattened) mesh that CuraEngine sliced.

Usage:
    python3 engine_prototype.py --address 127.0.0.1 --port 50051

CuraEngine connects as gRPC client; this plugin runs as server.
"""

import argparse
import logging
import math
import struct
import sys
import os
from concurrent import futures
from dataclasses import dataclass
from typing import List, Optional

import grpc

# Add proto output directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "proto"))

from cura.plugins.v0 import slot_id_pb2
from cura.plugins.v0 import printfeatures_pb2
from cura.plugins.slots.handshake.v0 import handshake_pb2, handshake_pb2_grpc
from cura.plugins.slots.broadcast.v0 import broadcast_pb2, broadcast_pb2_grpc
from cura.plugins.slots.gcode_paths.v0 import modify_pb2, modify_pb2_grpc
from google.protobuf import empty_pb2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("nonplanar_engine")

PLUGIN_NAME = "NonPlanarSlicing"
PLUGIN_VERSION = "1.0.0"
SLOT_VERSION = "0.1.0-alpha"

# Features that should NOT be inverse-transformed.
SKIP_FEATURES = frozenset([
    printfeatures_pb2.SUPPORT,
    printfeatures_pb2.SKIRTBRIM,
    printfeatures_pb2.SUPPORTINFILL,
    printfeatures_pb2.MOVEUNRETRACTED,
    printfeatures_pb2.MOVERETRACTED,
    printfeatures_pb2.SUPPORTINTERFACE,
    printfeatures_pb2.PRIMETOWER,
    printfeatures_pb2.MOVEWHILERETRACTING,
    printfeatures_pb2.MOVEWHILEUNRETRACTING,
    printfeatures_pb2.STATIONARYRETRACTUNRETRACT,
])

# Flow ratio bounds.
MIN_FLOW_RATIO = 0.5
MAX_FLOW_RATIO = 2.0


# ---------------------------------------------------------------------------
# Deformation Field (Python implementation)
# ---------------------------------------------------------------------------

NPDF_MAGIC = b"NPDF"
NPDF_VERSION = 1


@dataclass
class DeformationField:
    """Deserialized deformation field for inverse Z transform."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    resolution: float
    num_layers: int
    rows: int
    cols: int
    z_levels: List[float]       # [num_layers]
    displacements: List[float]  # [num_layers * rows * cols], row-major

    @classmethod
    def from_bytes(cls, data: bytes) -> "DeformationField":
        """Deserialize from NPDF binary format."""
        if len(data) < 58:
            raise ValueError("Data too short for NPDF header")

        magic = data[0:4]
        if magic != NPDF_MAGIC:
            raise ValueError(f"Invalid magic: {magic!r}")

        version = struct.unpack_from("<H", data, 4)[0]
        if version != NPDF_VERSION:
            raise ValueError(f"Unsupported version: {version}")

        num_layers, rows, cols = struct.unpack_from("<III", data, 6)
        x_min, x_max, y_min, y_max, resolution = struct.unpack_from("<5d", data, 18)

        header_size = 58
        z_start = header_size
        z_levels = list(struct.unpack_from(f"<{num_layers}f", data, z_start))

        d_start = z_start + num_layers * 4
        total_disp = num_layers * rows * cols
        displacements = list(struct.unpack_from(f"<{total_disp}f", data, d_start))

        return cls(
            x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
            resolution=resolution, num_layers=num_layers,
            rows=rows, cols=cols,
            z_levels=z_levels, displacements=displacements,
        )

    def in_bounds(self, x: float, y: float) -> bool:
        half = self.resolution * 0.5
        return (self.x_min - half <= x <= self.x_max + half and
                self.y_min - half <= y <= self.y_max + half)

    def _find_z_level_index(self, z: float) -> int:
        """Find rightmost index where z_levels[i] <= z."""
        lo, hi = 0, self.num_layers
        while lo < hi:
            mid = (lo + hi) // 2
            if self.z_levels[mid] <= z:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1)

    def interpolate(self, x: float, y: float, z: float) -> float:
        """Trilinear interpolation of displacement at world (x, y, z)."""
        if not self.in_bounds(x, y):
            return 0.0

        rows, cols = self.rows, self.cols

        cx = (x - self.x_min) / self.resolution
        cy = (y - self.y_min) / self.resolution

        c0 = max(0, min(int(math.floor(cx)), cols - 1))
        c1 = min(c0 + 1, cols - 1)
        r0 = max(0, min(int(math.floor(cy)), rows - 1))
        r1 = min(r0 + 1, rows - 1)

        fx = max(0.0, min(1.0, cx - math.floor(cx)))
        fy = max(0.0, min(1.0, cy - math.floor(cy)))

        z_idx_low = self._find_z_level_index(z)
        z_idx_high = min(z_idx_low + 1, self.num_layers - 1)

        if z_idx_low == z_idx_high:
            fz = 0.0
        else:
            z_low = self.z_levels[z_idx_low]
            z_high = self.z_levels[z_idx_high]
            dz = z_high - z_low
            fz = max(0.0, min(1.0, (z - z_low) / dz)) if dz > 1e-9 else 0.0

        def bilinear(layer_idx: int) -> float:
            base = layer_idx * rows * cols
            v00 = self.displacements[base + r0 * cols + c0]
            v01 = self.displacements[base + r0 * cols + c1]
            v10 = self.displacements[base + r1 * cols + c0]
            v11 = self.displacements[base + r1 * cols + c1]
            return (v00 * (1 - fx) * (1 - fy)
                    + v01 * fx * (1 - fy)
                    + v10 * (1 - fx) * fy
                    + v11 * fx * fy)

        d_low = bilinear(z_idx_low)
        d_high = bilinear(z_idx_high)
        return d_low * (1 - fz) + d_high * fz

    def inverse_z(self, x: float, y: float, z_deformed: float) -> float:
        """Find original Z given deformed Z using Newton's method."""
        z_guess = z_deformed

        for _ in range(20):
            disp = self.interpolate(x, y, z_guess)
            residual = z_guess + disp - z_deformed

            if abs(residual) < 0.0001:
                return z_guess

            eps = 0.001
            disp_plus = self.interpolate(x, y, z_guess + eps)
            deriv = 1.0 + (disp_plus - disp) / eps

            if abs(deriv) < 1e-12:
                return z_deformed - disp

            z_guess -= residual / deriv

        return z_guess


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class NonPlanarSettings:
    def __init__(self):
        self.enabled: bool = False
        self.deformation_field: Optional[DeformationField] = None


# ---------------------------------------------------------------------------
# gRPC Services
# ---------------------------------------------------------------------------

class HandshakeServicer(handshake_pb2_grpc.HandshakeServiceServicer):
    def Call(self, request, context):
        logger.info(
            "Handshake: slot=%s engine_plugin=%s v=%s",
            request.slot_id, request.plugin_name, request.version,
        )
        context.send_initial_metadata((
            ("cura-slot-version", SLOT_VERSION),
            ("cura-plugin-name", PLUGIN_NAME),
            ("cura-plugin-version", PLUGIN_VERSION),
        ))
        return handshake_pb2.CallResponse(
            slot_version_range=SLOT_VERSION,
            plugin_name=PLUGIN_NAME,
            plugin_version=PLUGIN_VERSION,
            broadcast_subscriptions=[slot_id_pb2.SETTINGS_BROADCAST],
        )


class BroadcastServicer(broadcast_pb2_grpc.BroadcastServiceServicer):
    def __init__(self, settings: NonPlanarSettings) -> None:
        self._settings = settings

    def BroadcastSettings(self, request, context):
        logger.info("Received settings broadcast")
        self._parse_settings(request.global_settings)
        for ext_settings in request.extruder_settings:
            self._parse_settings(ext_settings)
        logger.info(
            "NonPlanarSettings: enabled=%s has_field=%s",
            self._settings.enabled,
            self._settings.deformation_field is not None,
        )
        return empty_pb2.Empty()

    def _parse_settings(self, settings_msg) -> None:
        if settings_msg is None:
            return
        settings_map = settings_msg.settings
        for name, value_bytes in settings_map.items():
            if name == "nonplanar_enabled":
                val = value_bytes.decode("utf-8", errors="replace").strip()
                self._settings.enabled = val.lower() in ("true", "1", "yes")
            elif name == "nonplanar_deformation_field":
                if value_bytes:
                    try:
                        # Try zstd decompression first
                        raw = self._try_decompress(value_bytes)
                        self._settings.deformation_field = DeformationField.from_bytes(raw)
                        logger.info(
                            "Loaded deformation field: %d layers, %dx%d grid",
                            self._settings.deformation_field.num_layers,
                            self._settings.deformation_field.rows,
                            self._settings.deformation_field.cols,
                        )
                    except Exception as e:
                        logger.error("Failed to load deformation field: %s", e)

    @staticmethod
    def _try_decompress(data: bytes) -> bytes:
        """Try zstd decompression, fall back to raw data."""
        if data[:4] == b"\x28\xB5\x2F\xFD":
            try:
                import zstandard
                dctx = zstandard.ZstdDecompressor()
                return dctx.decompress(data)
            except ImportError:
                try:
                    import zstd
                    return zstd.decompress(data)
                except ImportError:
                    raise RuntimeError(
                        "zstd decompression needed but neither "
                        "zstandard nor zstd package is installed"
                    )
        return data


class GCodePathsModifyServicer(modify_pb2_grpc.GCodePathsModifyServiceServicer):
    """Applies inverse deformation to path Z coordinates."""

    def __init__(self, settings: NonPlanarSettings) -> None:
        self._settings = settings

    def Call(self, request, context):
        context.send_initial_metadata((
            ("cura-slot-version", SLOT_VERSION),
        ))

        paths = list(request.gcode_paths)

        if not self._settings.enabled or self._settings.deformation_field is None:
            return modify_pb2.CallResponse(gcode_paths=paths)

        field = self._settings.deformation_field
        modified = self._transform_paths(paths, request.layer_nr, field)
        return modify_pb2.CallResponse(gcode_paths=modified)

    def _transform_paths(self, paths, layer_nr, field):
        for path in paths:
            if path.feature in SKIP_FEATURES:
                continue

            if not path.path or not path.path.path:
                continue

            points = path.path.path

            # Compute flow adjustment from midpoint displacement gradient
            if points:
                mid = points[len(points) // 2]
                mx, my, mz = mid.x / 1000.0, mid.y / 1000.0, mid.z / 1000.0
                disp = field.interpolate(mx, my, mz)
                eps = 0.001
                disp_above = field.interpolate(mx, my, mz + eps)
                thickness_scale = 1.0 + (disp_above - disp) / eps
                if thickness_scale > 0:
                    ratio = max(MIN_FLOW_RATIO, min(MAX_FLOW_RATIO, thickness_scale))
                    path.flow_ratio *= ratio

            # Inverse-transform Z for every point
            for point in points:
                x_mm = point.x / 1000.0
                y_mm = point.y / 1000.0
                z_mm = point.z / 1000.0

                z_original = field.inverse_z(x_mm, y_mm, z_mm)
                point.z = round(z_original * 1000.0)

        if layer_nr % 50 == 0:
            logger.info("Layer %d: transformed %d paths", layer_nr, len(paths))

        return paths


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def serve(address: str, port: int) -> None:
    settings = NonPlanarSettings()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))

    handshake_pb2_grpc.add_HandshakeServiceServicer_to_server(
        HandshakeServicer(), server
    )
    broadcast_pb2_grpc.add_BroadcastServiceServicer_to_server(
        BroadcastServicer(settings), server
    )
    modify_pb2_grpc.add_GCodePathsModifyServiceServicer_to_server(
        GCodePathsModifyServicer(settings), server
    )

    listen_addr = f"{address}:{port}"
    server.add_insecure_port(listen_addr)
    server.start()
    logger.info("Non-Planar Slicing engine plugin listening on %s", listen_addr)

    server.wait_for_termination()


def main():
    parser = argparse.ArgumentParser(description="Non-Planar Slicing CuraEngine plugin")
    parser.add_argument("--address", default="127.0.0.1", help="Listen address")
    parser.add_argument("--port", type=int, required=True, help="Listen port")
    args = parser.parse_args()

    serve(args.address, args.port)


if __name__ == "__main__":
    main()

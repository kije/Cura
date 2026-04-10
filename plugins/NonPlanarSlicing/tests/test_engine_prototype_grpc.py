# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""End-to-end gRPC integration test for the engine_prototype.py.

These tests start the actual Python gRPC server (the same one used as
fallback when the Rust binary is unavailable), connect a client, and
exercise the full request/response cycle:

1. Handshake — verify version metadata and broadcast subscriptions
2. Broadcast settings — send the deformation field via file path
3. GCodePathsModify — send synthetic GCodePath data and verify the
   inverse Z transform produces correct results

This catches integration bugs in the engine plugin layer that the
Python-only pipeline tests miss, such as:
- gRPC service implementation errors
- Settings parsing errors (file path encoding, etc.)
- Path filtering by feature type
- Protobuf serialization/deserialization issues
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import tempfile
import threading
import time
from concurrent import futures
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

# Make engine_prototype.py and its proto modules importable
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_PLUGIN_ROOT / "proto"))

# Skip module if grpc is not installed
grpc = pytest.importorskip("grpc")

# Skip module if proto modules can't be loaded (need grpcio-tools to compile)
try:
    from cura.plugins.v0 import slot_id_pb2, printfeatures_pb2, point3d_pb2
    from cura.plugins.v0 import polygons_pb2, gcode_path_pb2
    from cura.plugins.slots.handshake.v0 import handshake_pb2, handshake_pb2_grpc
    from cura.plugins.slots.broadcast.v0 import broadcast_pb2, broadcast_pb2_grpc
    from cura.plugins.slots.gcode_paths.v0 import modify_pb2, modify_pb2_grpc
    from google.protobuf import empty_pb2
    _HAS_PROTOS = True
except ImportError:
    _HAS_PROTOS = False

if not _HAS_PROTOS:
    pytest.skip(
        "proto _pb2 modules not available — run grpc_tools.protoc to compile",
        allow_module_level=True,
    )

# Import engine_prototype components
from engine_prototype import (
    NonPlanarSettings,
    HandshakeServicer,
    BroadcastServicer,
    GCodePathsModifyServicer,
    DeformationField as ProtoDeformationField,
    SLOT_VERSION,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)


# ---------------------------------------------------------------------------
# NPDF serialization helper (mirrors NonPlanarSlicingExtension)
# ---------------------------------------------------------------------------

def serialize_npdf(
    num_layers: int,
    rows: int,
    cols: int,
    x_min: float, x_max: float,
    y_min: float, y_max: float,
    resolution: float,
    z_levels: np.ndarray,
    displacements: np.ndarray,
) -> bytes:
    header = struct.pack(
        "<4sHIII5d",
        b"NPDF", 1,
        num_layers, rows, cols,
        x_min, x_max, y_min, y_max, resolution,
    )
    return (
        header
        + z_levels.astype(np.float32).tobytes()
        + displacements.astype(np.float32).tobytes()
    )


def make_test_field_bytes() -> bytes:
    """Create a small deformation field in NPDF format for testing.

    Field: 5 layers × 4×4 grid covering (0..3, 0..3) mm.
    Uniform 0.1mm displacement throughout the safe region.
    """
    num_layers = 5
    rows, cols = 4, 4
    z_levels = np.linspace(0.2, 1.0, num_layers, dtype=np.float32)
    displacements = np.full((num_layers, rows, cols), 0.1, dtype=np.float32)
    return serialize_npdf(
        num_layers=num_layers, rows=rows, cols=cols,
        x_min=0.0, x_max=3.0,
        y_min=0.0, y_max=3.0,
        resolution=1.0,
        z_levels=z_levels,
        displacements=displacements,
    )


# ---------------------------------------------------------------------------
# gRPC server fixtures
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Find an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def grpc_server():
    """Start a fresh engine prototype gRPC server for each test."""
    settings = NonPlanarSettings()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))

    handshake_pb2_grpc.add_HandshakeServiceServicer_to_server(
        HandshakeServicer(), server
    )
    broadcast_pb2_grpc.add_BroadcastServiceServicer_to_server(
        BroadcastServicer(settings), server
    )
    modify_pb2_grpc.add_GCodePathsModifyServiceServicer_to_server(
        GCodePathsModifyServicer(settings), server
    )

    port = find_free_port()
    server.add_insecure_port(f"127.0.0.1:{port}")
    server.start()

    yield {"port": port, "server": server, "settings": settings}

    server.stop(grace=0.5)


@pytest.fixture
def grpc_channel(grpc_server):
    """Connect a gRPC channel to the test server."""
    channel = grpc.insecure_channel(f"127.0.0.1:{grpc_server['port']}")
    yield channel
    channel.close()


# ---------------------------------------------------------------------------
# Test: Handshake
# ---------------------------------------------------------------------------

class TestHandshake:
    def test_handshake_returns_plugin_metadata(self, grpc_channel):
        stub = handshake_pb2_grpc.HandshakeServiceStub(grpc_channel)
        request = handshake_pb2.CallRequest(
            slot_id=slot_id_pb2.GCODE_PATHS_MODIFY,
            version="0.1.0-alpha",
            plugin_name="CuraEngine",
            plugin_version="5.0.0",
        )
        response = stub.Call(request)
        assert response.plugin_name == PLUGIN_NAME
        assert response.plugin_version == PLUGIN_VERSION
        assert response.slot_version_range == SLOT_VERSION

    def test_handshake_subscribes_to_settings_broadcast(self, grpc_channel):
        stub = handshake_pb2_grpc.HandshakeServiceStub(grpc_channel)
        response = stub.Call(handshake_pb2.CallRequest(
            slot_id=slot_id_pb2.GCODE_PATHS_MODIFY,
            version="0.1.0-alpha",
            plugin_name="CuraEngine",
            plugin_version="5.0.0",
        ))
        assert slot_id_pb2.SETTINGS_BROADCAST in response.broadcast_subscriptions


# ---------------------------------------------------------------------------
# Test: Settings broadcast (deformation field via file)
# ---------------------------------------------------------------------------

class TestBroadcastSettings:
    def test_broadcast_loads_field_from_file(self, grpc_channel, grpc_server, tmp_path):
        """Sending a file path setting should cause the field to load."""
        field_path = tmp_path / "test_field.bin"
        field_path.write_bytes(make_test_field_bytes())

        settings_msg = broadcast_pb2.Settings(
            settings={
                "nonplanar_enabled": b"true",
                "nonplanar_deformation_field": str(field_path).encode("utf-8"),
            }
        )

        stub = broadcast_pb2_grpc.BroadcastServiceStub(grpc_channel)
        request = broadcast_pb2.BroadcastServiceSettingsRequest(
            global_settings=settings_msg,
        )
        stub.BroadcastSettings(request)

        # Verify the server-side state was updated
        assert grpc_server["settings"].enabled is True
        assert grpc_server["settings"].deformation_field is not None
        df = grpc_server["settings"].deformation_field
        assert df.num_layers == 5
        assert df.rows == 4
        assert df.cols == 4

    def test_broadcast_disabled_state(self, grpc_channel, grpc_server):
        """Plugin starts disabled and stays disabled until enabled."""
        stub = broadcast_pb2_grpc.BroadcastServiceStub(grpc_channel)
        stub.BroadcastSettings(broadcast_pb2.BroadcastServiceSettingsRequest(
            global_settings=broadcast_pb2.Settings(
                settings={"nonplanar_enabled": b"false"},
            ),
        ))
        assert grpc_server["settings"].enabled is False

    def test_broadcast_missing_file_handled_gracefully(self, grpc_channel, grpc_server):
        """A bad file path should not crash the server."""
        stub = broadcast_pb2_grpc.BroadcastServiceStub(grpc_channel)
        stub.BroadcastSettings(broadcast_pb2.BroadcastServiceSettingsRequest(
            global_settings=broadcast_pb2.Settings(
                settings={
                    "nonplanar_enabled": b"true",
                    "nonplanar_deformation_field": b"/nonexistent/path.bin",
                },
            ),
        ))
        # Server should still be alive — the field just won't be loaded
        assert grpc_server["settings"].enabled is True
        assert grpc_server["settings"].deformation_field is None


# ---------------------------------------------------------------------------
# Test: GCodePathsModify (the core inverse Z transform)
# ---------------------------------------------------------------------------

def make_gcode_path(
    points_mm: list,
    feature: int = printfeatures_pb2.OUTERWALL,
    line_width_um: int = 400,
    layer_thickness_um: int = 200,
    flow_ratio: float = 1.0,
) -> gcode_path_pb2.GCodePath:
    """Build a GCodePath protobuf from a list of (x, y, z) tuples in mm."""
    open_path = polygons_pb2.OpenPath()
    for x_mm, y_mm, z_mm in points_mm:
        pt = open_path.path.add()
        pt.x = int(round(x_mm * 1000))
        pt.y = int(round(y_mm * 1000))
        pt.z = int(round(z_mm * 1000))
    return gcode_path_pb2.GCodePath(
        path=open_path,
        feature=feature,
        line_width=line_width_um,
        layer_thickness=layer_thickness_um,
        flow_ratio=flow_ratio,
    )


class TestGCodePathsModify:
    @pytest.fixture
    def configured_server(self, grpc_channel, grpc_server, tmp_path):
        """Set up a server with a known deformation field loaded."""
        field_path = tmp_path / "test_field.bin"
        field_path.write_bytes(make_test_field_bytes())

        broadcast_stub = broadcast_pb2_grpc.BroadcastServiceStub(grpc_channel)
        broadcast_stub.BroadcastSettings(
            broadcast_pb2.BroadcastServiceSettingsRequest(
                global_settings=broadcast_pb2.Settings(
                    settings={
                        "nonplanar_enabled": b"true",
                        "nonplanar_deformation_field": str(field_path).encode("utf-8"),
                    },
                ),
            )
        )
        return grpc_server

    def test_modify_passthrough_when_disabled(self, grpc_channel, grpc_server):
        """Disabled plugin should return paths unchanged."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)
        path = make_gcode_path([(1.0, 1.0, 0.5), (2.0, 2.0, 0.5)])
        original_z_values = [p.z for p in path.path.path]

        request = modify_pb2.CallRequest(
            gcode_paths=[path],
            extruder_nr=0,
            layer_nr=2,
        )
        response = modify_stub.Call(request)

        assert len(response.gcode_paths) == 1
        result_z_values = [p.z for p in response.gcode_paths[0].path.path]
        assert result_z_values == original_z_values

    def test_modify_transforms_z_when_enabled(self, grpc_channel, configured_server):
        """When enabled with a field, Z values should be inverse-transformed."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        # Create a path inside the field bounds (uniform 0.1mm displacement)
        # Z = 0.6mm in deformed space → should become 0.5mm after inverse
        path = make_gcode_path([
            (1.0, 1.0, 0.6),
            (2.0, 2.0, 0.6),
        ])
        request = modify_pb2.CallRequest(
            gcode_paths=[path],
            extruder_nr=0,
            layer_nr=2,
        )
        response = modify_stub.Call(request)

        result_pts = response.gcode_paths[0].path.path
        # Inverse Z = 0.6 - 0.1 = 0.5mm = 500 microns
        for pt in result_pts:
            assert pt.z == pytest.approx(500, abs=2)

    def test_modify_skips_support_features(self, grpc_channel, configured_server):
        """SUPPORT/TRAVEL features should not be Z-transformed."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        # Create a SUPPORT path inside the field
        support_path = make_gcode_path(
            [(1.0, 1.0, 0.6), (2.0, 2.0, 0.6)],
            feature=printfeatures_pb2.SUPPORT,
        )
        original_z = [p.z for p in support_path.path.path]

        request = modify_pb2.CallRequest(
            gcode_paths=[support_path],
            extruder_nr=0,
            layer_nr=2,
        )
        response = modify_stub.Call(request)

        result_z = [p.z for p in response.gcode_paths[0].path.path]
        # Support paths should pass through unchanged
        assert result_z == original_z

    def test_modify_skips_travel_moves(self, grpc_channel, configured_server):
        """MOVERETRACTED / MOVEUNRETRACTED should pass through unchanged."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        for feature in (
            printfeatures_pb2.MOVERETRACTED,
            printfeatures_pb2.MOVEUNRETRACTED,
        ):
            path = make_gcode_path(
                [(1.0, 1.0, 0.6), (2.0, 2.0, 0.6)],
                feature=feature,
            )
            original_z = [p.z for p in path.path.path]
            response = modify_stub.Call(modify_pb2.CallRequest(
                gcode_paths=[path],
                extruder_nr=0,
                layer_nr=2,
            ))
            result_z = [p.z for p in response.gcode_paths[0].path.path]
            assert result_z == original_z, \
                f"Feature {feature} should not be transformed"

    def test_modify_handles_multiple_paths(self, grpc_channel, configured_server):
        """Multiple paths in one request should all be processed."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        paths = [
            make_gcode_path(
                [(0.5, 0.5, 0.6), (1.5, 1.5, 0.6)],
                feature=printfeatures_pb2.OUTERWALL,
            ),
            make_gcode_path(
                [(2.0, 2.0, 0.6), (2.5, 2.5, 0.6)],
                feature=printfeatures_pb2.INNERWALL,
            ),
            make_gcode_path(
                [(1.0, 1.0, 0.6), (2.0, 2.0, 0.6)],
                feature=printfeatures_pb2.INFILL,
            ),
        ]
        response = modify_stub.Call(modify_pb2.CallRequest(
            gcode_paths=paths,
            extruder_nr=0,
            layer_nr=2,
        ))

        assert len(response.gcode_paths) == 3
        # All printable features should be transformed
        for p in response.gcode_paths:
            for pt in p.path.path:
                assert pt.z == pytest.approx(500, abs=10)

    def test_modify_outside_field_bounds_passes_through(self, grpc_channel, configured_server):
        """Points outside the field XY bounds should not be modified."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        # Point at (100, 100) is way outside the (0..3, 0..3) field
        path = make_gcode_path([(100.0, 100.0, 0.6), (101.0, 101.0, 0.6)])
        response = modify_stub.Call(modify_pb2.CallRequest(
            gcode_paths=[path],
            extruder_nr=0,
            layer_nr=2,
        ))
        result_pts = response.gcode_paths[0].path.path
        # Outside bounds: displacement = 0, so Z unchanged at 600 microns
        for pt in result_pts:
            assert pt.z == pytest.approx(600, abs=2)

    def test_modify_adjusts_flow_ratio(self, grpc_channel, configured_server):
        """Flow ratio should be adjusted (still in [MIN, MAX] bounds)."""
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)

        path = make_gcode_path(
            [(1.0, 1.0, 0.6), (2.0, 2.0, 0.6)],
            flow_ratio=1.0,
        )
        response = modify_stub.Call(modify_pb2.CallRequest(
            gcode_paths=[path],
            extruder_nr=0,
            layer_nr=2,
        ))
        result = response.gcode_paths[0]
        # Flow ratio should still be a sensible value (uniform field
        # gives thickness_scale ≈ 1.0, so ratio ≈ 1.0)
        assert 0.5 <= result.flow_ratio <= 2.0


# ---------------------------------------------------------------------------
# Test: Multi-call sequence (handshake → broadcast → modify)
# ---------------------------------------------------------------------------

class TestFullSession:
    """Simulate a full CuraEngine session lifecycle."""

    def test_full_session_lifecycle(self, grpc_channel, grpc_server, tmp_path):
        # 1. Handshake
        handshake_stub = handshake_pb2_grpc.HandshakeServiceStub(grpc_channel)
        hs_response = handshake_stub.Call(handshake_pb2.CallRequest(
            slot_id=slot_id_pb2.GCODE_PATHS_MODIFY,
            version="0.1.0-alpha",
            plugin_name="CuraEngine",
            plugin_version="5.0.0",
        ))
        assert hs_response.plugin_name == PLUGIN_NAME

        # 2. Broadcast settings
        field_path = tmp_path / "session_field.bin"
        field_path.write_bytes(make_test_field_bytes())
        broadcast_stub = broadcast_pb2_grpc.BroadcastServiceStub(grpc_channel)
        broadcast_stub.BroadcastSettings(
            broadcast_pb2.BroadcastServiceSettingsRequest(
                global_settings=broadcast_pb2.Settings(
                    settings={
                        "nonplanar_enabled": b"true",
                        "nonplanar_deformation_field": str(field_path).encode("utf-8"),
                    },
                ),
            )
        )
        assert grpc_server["settings"].enabled is True
        assert grpc_server["settings"].deformation_field is not None

        # 3. Send a sequence of layer modify requests
        modify_stub = modify_pb2_grpc.GCodePathsModifyServiceStub(grpc_channel)
        for layer_nr in range(5):
            path = make_gcode_path(
                [(1.0, 1.0, 0.6), (2.0, 2.0, 0.6)],
                feature=printfeatures_pb2.OUTERWALL,
            )
            response = modify_stub.Call(modify_pb2.CallRequest(
                gcode_paths=[path],
                extruder_nr=0,
                layer_nr=layer_nr,
            ))
            assert len(response.gcode_paths) == 1
            # All layers should produce a transformed result
            for pt in response.gcode_paths[0].path.path:
                assert pt.z != 600  # Should have been transformed

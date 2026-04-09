//! Non-Planar Slicing algorithm — compiled to WASI.
//!
//! Pure computation: takes serialized protobuf request bytes,
//! returns serialized protobuf response bytes. No networking.
//!
//! The host (native binary) handles gRPC and calls these functions
//! via wasmtime.
//!
//! ## Algorithm
//!
//! The mesh was forward-deformed (flattened) before CuraEngine sliced it.
//! CuraEngine sliced with flat layers through the deformed mesh.
//! This module applies the **inverse deformation** to restore the
//! original curved Z coordinates on every path point.
//!
//! For each Point3D(x, y, z_sliced):
//!   displacement = deformation_field.interpolate(x, y, z_sliced)
//!   z_original = z_sliced - displacement
//!
//! The deformation field is a 3D grid of Z displacements, passed
//! from the Python side via settings broadcast as compressed binary.

use prost::Message;

pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/cura.plugins.v0.rs"));
}

/// PrintFeature values matching the protobuf definition.
/// Features that should NOT be inverse-transformed.
const SUPPORT: i32 = 4;
const SKIRTBRIM: i32 = 5;
const SUPPORTINFILL: i32 = 7;
const MOVEUNRETRACTED: i32 = 8;
const MOVERETRACTED: i32 = 9;
const SUPPORTINTERFACE: i32 = 10;
const PRIMETOWER: i32 = 11;
const MOVEWHILERETRACTING: i32 = 12;
const MOVEWHILEUNRETRACTING: i32 = 13;
const STATIONARYRETRACTUNRETRACT: i32 = 14;

/// PrintFeature 0 = unset/unknown in protobuf3, should not be transformed.
const NONETYPE: i32 = 0;

/// Returns true if this feature should have its Z coordinates transformed.
fn is_transformable_feature(feature: i32) -> bool {
    !matches!(
        feature,
        NONETYPE
            | SUPPORT
            | SKIRTBRIM
            | SUPPORTINFILL
            | MOVEUNRETRACTED
            | MOVERETRACTED
            | SUPPORTINTERFACE
            | PRIMETOWER
            | MOVEWHILERETRACTING
            | MOVEWHILEUNRETRACTING
            | STATIONARYRETRACTUNRETRACT
    )
}

// ---------------------------------------------------------------------------
// Deformation Field
// ---------------------------------------------------------------------------

/// Binary format magic bytes.
const MAGIC: &[u8; 4] = b"NPDF";
/// Expected format version.
const FORMAT_VERSION: u16 = 1;

/// Deserialized deformation field for inverse Z transform.
struct DeformationField {
    x_min: f64,
    x_max: f64,
    y_min: f64,
    y_max: f64,
    resolution: f64,
    num_layers: usize,
    rows: usize,
    cols: usize,
    z_levels: Vec<f32>,
    /// Flat array: displacements[layer * rows * cols + row * cols + col]
    displacements: Vec<f32>,
}

impl DeformationField {
    /// Deserialize from the binary NPDF format.
    ///
    /// Layout (little-endian):
    ///   magic: [u8; 4] = b"NPDF"
    ///   version: u16
    ///   num_layers: u32
    ///   rows: u32
    ///   cols: u32
    ///   x_min: f64, x_max: f64, y_min: f64, y_max: f64, resolution: f64
    ///   z_levels: [f32; num_layers]
    ///   displacements: [f32; num_layers * rows * cols]
    fn from_bytes(data: &[u8]) -> Result<Self, &'static str> {
        if data.len() < 58 {
            return Err("Data too short for header");
        }

        // Parse header
        if &data[0..4] != MAGIC {
            return Err("Invalid magic bytes");
        }
        let version = u16::from_le_bytes([data[4], data[5]]);
        if version != FORMAT_VERSION {
            return Err("Unsupported format version");
        }

        let num_layers = u32::from_le_bytes(data[6..10].try_into().unwrap()) as usize;
        let rows = u32::from_le_bytes(data[10..14].try_into().unwrap()) as usize;
        let cols = u32::from_le_bytes(data[14..18].try_into().unwrap()) as usize;

        let x_min = f64::from_le_bytes(data[18..26].try_into().unwrap());
        let x_max = f64::from_le_bytes(data[26..34].try_into().unwrap());
        let y_min = f64::from_le_bytes(data[34..42].try_into().unwrap());
        let y_max = f64::from_le_bytes(data[42..50].try_into().unwrap());
        let resolution = f64::from_le_bytes(data[50..58].try_into().unwrap());

        let header_size = 58;
        let z_levels_size = num_layers * 4;
        let disp_size = num_layers * rows * cols * 4;
        let expected = header_size + z_levels_size + disp_size;

        if data.len() < expected {
            return Err("Data too short for payload");
        }

        // Parse z_levels
        let z_start = header_size;
        let mut z_levels = Vec::with_capacity(num_layers);
        for i in 0..num_layers {
            let off = z_start + i * 4;
            z_levels.push(f32::from_le_bytes(data[off..off + 4].try_into().unwrap()));
        }

        // Parse displacements
        let d_start = z_start + z_levels_size;
        let mut displacements = Vec::with_capacity(num_layers * rows * cols);
        for i in 0..(num_layers * rows * cols) {
            let off = d_start + i * 4;
            displacements.push(f32::from_le_bytes(data[off..off + 4].try_into().unwrap()));
        }

        Ok(DeformationField {
            x_min,
            x_max,
            y_min,
            y_max,
            resolution,
            num_layers,
            rows,
            cols,
            z_levels,
            displacements,
        })
    }

    /// Check if (x, y) is within the grid bounding box.
    fn in_bounds(&self, x: f64, y: f64) -> bool {
        let half = self.resolution * 0.5;
        x >= self.x_min - half
            && x <= self.x_max + half
            && y >= self.y_min - half
            && y <= self.y_max + half
    }

    /// Find the index of the z_level just below z.
    fn find_z_level_index(&self, z: f32) -> usize {
        // Binary search: find rightmost index where z_levels[i] <= z
        let mut lo = 0usize;
        let mut hi = self.num_layers;
        while lo < hi {
            let mid = (lo + hi) / 2;
            if self.z_levels[mid] <= z {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        if lo == 0 { 0 } else { lo - 1 }
    }

    /// Trilinear interpolation of displacement at world (x, y, z).
    ///
    /// Matches the Python `DeformationField.interpolate()` implementation.
    fn interpolate(&self, x: f64, y: f64, z: f64) -> f64 {
        if !self.in_bounds(x, y) {
            return 0.0;
        }

        let rows = self.rows;
        let cols = self.cols;

        // Continuous grid coordinates in XY
        let cx = (x - self.x_min) / self.resolution;
        let cy = (y - self.y_min) / self.resolution;

        // Integer corners for bilinear XY
        let c0 = (cx.floor() as isize).max(0).min(cols as isize - 1) as usize;
        let c1 = (c0 + 1).min(cols - 1);
        let r0 = (cy.floor() as isize).max(0).min(rows as isize - 1) as usize;
        let r1 = (r0 + 1).min(rows - 1);

        let fx = (cx - cx.floor()).clamp(0.0, 1.0);
        let fy = (cy - cy.floor()).clamp(0.0, 1.0);

        // Z interpolation between layers
        let z_f32 = z as f32;
        let z_idx_low = self.find_z_level_index(z_f32);
        let z_idx_high = (z_idx_low + 1).min(self.num_layers - 1);

        let fz = if z_idx_low == z_idx_high {
            0.0
        } else {
            let z_low = self.z_levels[z_idx_low] as f64;
            let z_high = self.z_levels[z_idx_high] as f64;
            let dz = z_high - z_low;
            if dz > 1e-9 {
                ((z - z_low) / dz).clamp(0.0, 1.0)
            } else {
                0.0
            }
        };

        // Bilinear interpolation for each Z level
        let bilinear = |layer_idx: usize| -> f64 {
            let base = layer_idx * rows * cols;
            let v00 = self.displacements[base + r0 * cols + c0] as f64;
            let v01 = self.displacements[base + r0 * cols + c1] as f64;
            let v10 = self.displacements[base + r1 * cols + c0] as f64;
            let v11 = self.displacements[base + r1 * cols + c1] as f64;
            v00 * (1.0 - fx) * (1.0 - fy)
                + v01 * fx * (1.0 - fy)
                + v10 * (1.0 - fx) * fy
                + v11 * fx * fy
        };

        let d_low = bilinear(z_idx_low);
        let d_high = bilinear(z_idx_high);
        d_low * (1.0 - fz) + d_high * fz
    }

    /// Inverse Z transform: given a deformed Z, find the original Z.
    ///
    /// Solves: z_orig + field(x, y, z_orig) = z_deformed
    /// using Newton's method.
    fn inverse_z(&self, x: f64, y: f64, z_deformed: f64) -> f64 {
        const MAX_ITER: usize = 20;
        const TOLERANCE: f64 = 0.0001; // 0.1 micron in mm

        let mut z_guess = z_deformed;

        for _ in 0..MAX_ITER {
            let disp = self.interpolate(x, y, z_guess);
            let residual = z_guess + disp - z_deformed;

            if residual.abs() < TOLERANCE {
                return z_guess;
            }

            // Numerical derivative
            let eps = 0.001;
            let disp_plus = self.interpolate(x, y, z_guess + eps);
            let deriv = 1.0 + (disp_plus - disp) / eps;

            if deriv.abs() < 1e-12 {
                return z_deformed - disp;
            }

            z_guess -= residual / deriv;
        }

        // Did not converge — return best guess
        z_guess
    }
}

// ---------------------------------------------------------------------------
// Core algorithm
// ---------------------------------------------------------------------------

/// Bounds for flow ratio adjustment.
const MIN_FLOW_RATIO: f64 = 0.5;
const MAX_FLOW_RATIO: f64 = 2.0;

/// Maximum segment length (microns) for subdivision before Z transform.
/// All references (CurviSlicer: 0.8mm, RotBot: 1-2mm) recommend subdivision
/// to accurately follow curved deformation fields.
const MAX_SEGMENT_LENGTH_UM: i64 = 800; // 0.8mm, matching CurviSlicer

/// Subdivide path segments longer than MAX_SEGMENT_LENGTH_UM.
///
/// This is critical for accuracy: without subdivision, long straight
/// segments between two inverse-transformed endpoints would cut through
/// the curved deformation field instead of following it.
fn subdivide_path(points: &[proto::Point3D]) -> Vec<proto::Point3D> {
    if points.len() < 2 {
        return points.to_vec();
    }

    let mut result = Vec::with_capacity(points.len() * 2);
    result.push(points[0].clone());

    for i in 1..points.len() {
        let a = &points[i - 1];
        let b = &points[i];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist_sq = dx * dx + dy * dy;
        let dist = (dist_sq as f64).sqrt() as i64;

        if dist > MAX_SEGMENT_LENGTH_UM {
            let n_segments = ((dist + MAX_SEGMENT_LENGTH_UM - 1) / MAX_SEGMENT_LENGTH_UM).max(2);
            let dz = b.z - a.z;
            for j in 1..n_segments {
                let t = j as f64 / n_segments as f64;
                result.push(proto::Point3D {
                    x: a.x + (dx as f64 * t).round() as i64,
                    y: a.y + (dy as f64 * t).round() as i64,
                    z: a.z + (dz as f64 * t).round() as i64,
                });
            }
        }
        result.push(b.clone());
    }

    result
}

/// Apply inverse deformation to all path points.
///
/// For each printable path:
/// 1. Subdivide long segments for accurate surface following
/// 2. Inverse-transform Z coordinates from deformed to original space
/// 3. Adjust flow_ratio per segment for thickness compensation
pub fn modify_paths(
    mut paths: Vec<proto::GCodePath>,
    _layer_nr: i64,
    field: &DeformationField,
) -> Vec<proto::GCodePath> {
    for path in &mut paths {
        if !is_transformable_feature(path.feature) {
            continue;
        }

        let open_path = match path.path.as_mut() {
            Some(p) => p,
            None => continue,
        };

        if open_path.path.is_empty() {
            continue;
        }

        // Step 1: Subdivide long segments
        open_path.path = subdivide_path(&open_path.path);

        // Step 2: Compute flow adjustment from average thickness ratio.
        // Sample displacement gradient at multiple points along the path
        // for better accuracy than a single midpoint sample.
        let sample_count = open_path.path.len().min(10).max(1);
        let step = open_path.path.len() / sample_count;
        let mut total_thickness_scale = 0.0;
        let mut samples = 0;

        for idx in (0..open_path.path.len()).step_by(step.max(1)) {
            let pt = &open_path.path[idx];
            let px = pt.x as f64 / 1000.0;
            let py = pt.y as f64 / 1000.0;
            let pz = pt.z as f64 / 1000.0;

            let disp_here = field.interpolate(px, py, pz);
            let eps = 0.001;
            let disp_above = field.interpolate(px, py, pz + eps);
            let scale = 1.0 + (disp_above - disp_here) / eps;
            if scale > 0.0 {
                total_thickness_scale += scale;
                samples += 1;
            }
        }

        if samples > 0 {
            let avg_scale = total_thickness_scale / samples as f64;
            let ratio = avg_scale.clamp(MIN_FLOW_RATIO, MAX_FLOW_RATIO);
            path.flow_ratio *= ratio;
        }

        // Step 3: Inverse-transform Z for every point
        for point in &mut open_path.path {
            let x_mm = point.x as f64 / 1000.0;
            let y_mm = point.y as f64 / 1000.0;
            let z_mm = point.z as f64 / 1000.0;

            let z_original = field.inverse_z(x_mm, y_mm, z_mm);
            point.z = (z_original * 1000.0).round() as i64;
        }
    }

    paths
}

// ---------------------------------------------------------------------------
// Request/Response protobuf wrappers
// ---------------------------------------------------------------------------

/// Request matching the gRPC CallRequest.
#[derive(Clone, Message)]
pub struct ModifyRequest {
    #[prost(message, repeated, tag = "1")]
    pub gcode_paths: Vec<proto::GCodePath>,
    #[prost(int64, tag = "2")]
    pub extruder_nr: i64,
    #[prost(int64, tag = "3")]
    pub layer_nr: i64,
}

/// Response matching the gRPC CallResponse.
#[derive(Clone, Message)]
pub struct ModifyResponse {
    #[prost(message, repeated, tag = "1")]
    pub gcode_paths: Vec<proto::GCodePath>,
}

// ---------------------------------------------------------------------------
// WASI FFI — called by the host via wasmtime
// ---------------------------------------------------------------------------

/// Global deformation field, written by the host before slicing.
static mut DEFORMATION_FIELD: Option<DeformationField> = None;

/// Whether the plugin is enabled.
static mut ENABLED: bool = false;

/// Allocate memory in the WASM module for the host to write into.
#[no_mangle]
pub extern "C" fn alloc(len: u32) -> *mut u8 {
    let mut buf = Vec::with_capacity(len as usize);
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    ptr
}

/// Free memory previously allocated by alloc.
#[no_mangle]
pub extern "C" fn dealloc(ptr: *mut u8, len: u32) {
    unsafe {
        drop(Vec::from_raw_parts(ptr, 0, len as usize));
    }
}

/// Set enabled state.
#[no_mangle]
pub extern "C" fn set_enabled(enabled: u32) {
    unsafe {
        ENABLED = enabled != 0;
    }
}

/// Load the deformation field from binary data.
///
/// Called by the host after receiving the serialized field via settings
/// broadcast. The data is the raw NPDF binary (already decompressed
/// by the host).
///
/// Returns 0 on success, 1 on error.
#[no_mangle]
pub extern "C" fn set_deformation_field(data_ptr: *const u8, data_len: u32) -> u32 {
    let data = unsafe { std::slice::from_raw_parts(data_ptr, data_len as usize) };

    match DeformationField::from_bytes(data) {
        Ok(field) => {
            unsafe {
                DEFORMATION_FIELD = Some(field);
            }
            0
        }
        Err(_) => 1,
    }
}

/// Process a layer's paths. Input/output are protobuf-encoded bytes.
///
/// The host writes the serialized ModifyRequest at `input_ptr`,
/// calls this function, then reads the serialized ModifyResponse
/// from the returned pointer. The response length is written to
/// `output_len_ptr`.
#[no_mangle]
pub extern "C" fn process_layer(
    input_ptr: *const u8,
    input_len: u32,
    output_len_ptr: *mut u32,
) -> *mut u8 {
    let input = unsafe { std::slice::from_raw_parts(input_ptr, input_len as usize) };

    let request = match ModifyRequest::decode(input) {
        Ok(r) => r,
        Err(_) => {
            unsafe { *output_len_ptr = 0; }
            return std::ptr::null_mut();
        }
    };

    let result = unsafe {
        if !ENABLED {
            request.gcode_paths
        } else {
            match &DEFORMATION_FIELD {
                Some(field) => modify_paths(request.gcode_paths, request.layer_nr, field),
                None => request.gcode_paths,
            }
        }
    };

    let response = ModifyResponse {
        gcode_paths: result,
    };

    let output = response.encode_to_vec();
    let len = output.len();
    let ptr = alloc(len as u32);
    unsafe {
        std::ptr::copy_nonoverlapping(output.as_ptr(), ptr, len);
        *output_len_ptr = len as u32;
    }
    ptr
}

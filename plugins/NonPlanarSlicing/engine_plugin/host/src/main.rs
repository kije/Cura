mod wasm_runtime;

use std::sync::{Arc, Mutex};

use clap::Parser;
use prost::Message;
use tonic::metadata::MetadataValue;
use tonic::transport::Server;
use tonic::{Request, Response, Status};
use tracing::{info, warn};

use wasm_runtime::WasmRuntime;

// Generated protobuf modules
pub mod proto {
    pub mod v0 {
        tonic::include_proto!("cura.plugins.v0");
    }
    pub mod handshake {
        tonic::include_proto!("cura.plugins.slots.handshake.v0");
    }
    pub mod broadcast {
        tonic::include_proto!("cura.plugins.slots.broadcast.v0");
    }
    pub mod gcode_paths {
        tonic::include_proto!("cura.plugins.slots.gcode_paths.v0.modify");
    }
}

const PLUGIN_NAME: &str = "NonPlanarSlicing";
const PLUGIN_VERSION: &str = "1.0.0";
const SLOT_VERSION: &str = "0.1.0-alpha";

fn slot_metadata() -> tonic::metadata::MetadataMap {
    let mut meta = tonic::metadata::MetadataMap::new();
    meta.insert("cura-slot-version", MetadataValue::from_static(SLOT_VERSION));
    meta.insert("cura-plugin-name", MetadataValue::from_static(PLUGIN_NAME));
    meta.insert(
        "cura-plugin-version",
        MetadataValue::from_static(PLUGIN_VERSION),
    );
    meta
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default)]
struct NonPlanarSettings {
    enabled: bool,
    /// Raw NPDF binary for the deformation field (loaded from file).
    deformation_field_data: Option<Vec<u8>>,
}

type SharedSettings = Arc<Mutex<NonPlanarSettings>>;

fn parse_settings(
    s: &mut NonPlanarSettings,
    settings_map: &std::collections::HashMap<String, Vec<u8>>,
) {
    for (name, value_bytes) in settings_map {
        match name.as_str() {
            "nonplanar_enabled" => {
                let val = String::from_utf8_lossy(value_bytes).trim().to_string();
                s.enabled = matches!(val.as_str(), "true" | "1" | "True" | "yes");
            }
            "nonplanar_deformation_field" => {
                // This is a file path (string) to the NPDF binary.
                // The Python side writes the binary to a temp file and passes
                // the path as a setting, because binary data cannot survive
                // the str() serialization in the settings broadcast pipeline.
                let path = String::from_utf8_lossy(value_bytes).trim().to_string();
                if !path.is_empty() {
                    match std::fs::read(&path) {
                        Ok(data) => {
                            info!("Loaded deformation field from {}: {} bytes", path, data.len());
                            s.deformation_field_data = Some(data);
                        }
                        Err(e) => {
                            warn!("Failed to read deformation field from {}: {}", path, e);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

// ---------------------------------------------------------------------------
// HandshakeService
// ---------------------------------------------------------------------------

struct HandshakeServiceImpl;

#[tonic::async_trait]
impl proto::handshake::handshake_service_server::HandshakeService for HandshakeServiceImpl {
    async fn call(
        &self,
        request: Request<proto::handshake::CallRequest>,
    ) -> Result<Response<proto::handshake::CallResponse>, Status> {
        let req = request.into_inner();
        info!(
            "Handshake: slot={} plugin={} version={}",
            req.slot_id, req.plugin_name, req.version,
        );
        let mut resp = Response::new(proto::handshake::CallResponse {
            slot_version_range: SLOT_VERSION.to_string(),
            plugin_name: PLUGIN_NAME.to_string(),
            plugin_version: PLUGIN_VERSION.to_string(),
            broadcast_subscriptions: vec![proto::v0::SlotId::SettingsBroadcast.into()],
        });
        *resp.metadata_mut() = slot_metadata();
        Ok(resp)
    }
}

// ---------------------------------------------------------------------------
// BroadcastService
// ---------------------------------------------------------------------------

struct BroadcastServiceImpl {
    settings: SharedSettings,
    wasm: Arc<Mutex<WasmRuntime>>,
}

#[tonic::async_trait]
impl proto::broadcast::broadcast_service_server::BroadcastService for BroadcastServiceImpl {
    async fn broadcast_settings(
        &self,
        request: Request<proto::broadcast::BroadcastServiceSettingsRequest>,
    ) -> Result<Response<()>, Status> {
        let req = request.into_inner();
        info!("Received settings broadcast");

        let mut s = self.settings.lock().unwrap();
        if let Some(global) = &req.global_settings {
            parse_settings(&mut s, &global.settings);
        }
        for ext in &req.extruder_settings {
            parse_settings(&mut s, &ext.settings);
        }

        info!(
            "NonPlanarSettings: enabled={} has_field={}",
            s.enabled,
            s.deformation_field_data.is_some(),
        );

        // Push state to WASM module
        if let Ok(mut wasm) = self.wasm.lock() {
            wasm.set_enabled(s.enabled);

            if let Some(field_data) = &s.deformation_field_data {
                // Data is raw NPDF binary (loaded from file in parse_settings).
                let raw_data = field_data.clone();

                if !raw_data.is_empty() {
                    match wasm.set_deformation_field(&raw_data) {
                        Ok(()) => info!(
                            "Loaded deformation field into WASM ({} bytes)",
                            raw_data.len()
                        ),
                        Err(e) => warn!("Failed to load deformation field: {}", e),
                    }
                }
            }
        }

        Ok(Response::new(()))
    }
}

// ---------------------------------------------------------------------------
// GCodePathsModifyService — delegates to WASM
// ---------------------------------------------------------------------------

struct GCodePathsModifyServiceImpl {
    settings: SharedSettings,
    wasm: Arc<Mutex<WasmRuntime>>,
}

#[tonic::async_trait]
impl proto::gcode_paths::g_code_paths_modify_service_server::GCodePathsModifyService
    for GCodePathsModifyServiceImpl
{
    async fn call(
        &self,
        request: Request<proto::gcode_paths::CallRequest>,
    ) -> Result<Response<proto::gcode_paths::CallResponse>, Status> {
        let req = request.into_inner();
        let layer_nr = req.layer_nr;
        let path_count = req.gcode_paths.len();

        // Quick check: if disabled, skip WASM call entirely
        {
            let s = self.settings.lock().unwrap();
            if !s.enabled || s.deformation_field_data.is_none() {
                let mut r = Response::new(proto::gcode_paths::CallResponse {
                    gcode_paths: req.gcode_paths,
                });
                *r.metadata_mut() = slot_metadata();
                return Ok(r);
            }
        }

        // Serialize request to protobuf bytes
        let input_bytes = req.encode_to_vec();

        // Call WASM module
        let output_bytes = {
            let mut wasm = self.wasm.lock().unwrap();
            match wasm.process_layer(&input_bytes) {
                Ok(bytes) => bytes,
                Err(e) => {
                    warn!(
                        "Layer {}: WASM error: {}, returning paths unchanged",
                        layer_nr, e
                    );
                    let mut r = Response::new(proto::gcode_paths::CallResponse {
                        gcode_paths: req.gcode_paths,
                    });
                    *r.metadata_mut() = slot_metadata();
                    return Ok(r);
                }
            }
        };

        // Deserialize response
        let response = match proto::gcode_paths::CallResponse::decode(output_bytes.as_slice()) {
            Ok(r) => r,
            Err(e) => {
                warn!(
                    "Layer {}: protobuf decode error: {}, returning paths unchanged",
                    layer_nr, e
                );
                let mut r = Response::new(proto::gcode_paths::CallResponse {
                    gcode_paths: req.gcode_paths,
                });
                *r.metadata_mut() = slot_metadata();
                return Ok(r);
            }
        };

        if layer_nr % 50 == 0 {
            info!(
                "Layer {}: transformed {} paths",
                layer_nr, response.gcode_paths.len(),
            );
        }

        let mut r = Response::new(response);
        *r.metadata_mut() = slot_metadata();
        Ok(r)
    }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

#[derive(Parser, Debug)]
#[command(
    name = "nonplanar_engine",
    about = "Non-Planar Slicing CuraEngine plugin"
)]
struct Cli {
    #[arg(long, default_value = "127.0.0.1")]
    address: String,

    #[arg(long)]
    port: u16,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cli = Cli::parse();
    let addr = format!("{}:{}", cli.address, cli.port).parse()?;

    // Find the WASM module next to the executable
    let exe_dir = std::env::current_exe()?
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .to_path_buf();

    let wasm_path = [
        exe_dir.join("nonplanar.wasm"),
        exe_dir.join("../nonplanar.wasm"),
        std::path::PathBuf::from("nonplanar.wasm"),
    ]
    .into_iter()
    .find(|p| p.exists())
    .ok_or("Could not find nonplanar.wasm")?;

    info!("Loading WASM module from {:?}", wasm_path);
    let wasm = Arc::new(Mutex::new(WasmRuntime::new(&wasm_path)?));

    let settings: SharedSettings = Arc::new(Mutex::new(NonPlanarSettings::default()));

    info!("Non-Planar Slicing engine plugin listening on {}", addr);

    Server::builder()
        .add_service(
            proto::handshake::handshake_service_server::HandshakeServiceServer::new(
                HandshakeServiceImpl,
            ),
        )
        .add_service(
            proto::broadcast::broadcast_service_server::BroadcastServiceServer::new(
                BroadcastServiceImpl {
                    settings: settings.clone(),
                    wasm: wasm.clone(),
                },
            ),
        )
        .add_service(
            proto::gcode_paths::g_code_paths_modify_service_server::GCodePathsModifyServiceServer::new(
                GCodePathsModifyServiceImpl {
                    settings,
                    wasm,
                },
            ),
        )
        .serve(addr)
        .await?;

    Ok(())
}

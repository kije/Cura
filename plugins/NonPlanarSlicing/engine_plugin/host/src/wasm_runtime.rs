//! Wasmtime runtime for calling the Non-Planar Slicing WASM algorithm module.

use std::path::Path;
use wasmtime::*;
use wasmtime_wasi::preview1::WasiP1Ctx;
use wasmtime_wasi::WasiCtxBuilder;

pub struct WasmRuntime {
    store: Store<WasiP1Ctx>,
    instance: Instance,
    memory: Memory,
}

impl WasmRuntime {
    pub fn new(wasm_path: &Path) -> Result<Self, Box<dyn std::error::Error>> {
        let engine = Engine::default();
        let module = Module::from_file(&engine, wasm_path)?;

        let wasi_ctx = WasiCtxBuilder::new().build_p1();
        let mut store = Store::new(&engine, wasi_ctx);

        let mut linker = Linker::new(&engine);
        wasmtime_wasi::preview1::add_to_linker_sync(&mut linker, |ctx| ctx)?;

        let instance = linker.instantiate(&mut store, &module)?;

        let memory = instance
            .get_memory(&mut store, "memory")
            .ok_or("WASM module missing 'memory' export")?;

        Ok(Self {
            store,
            instance,
            memory,
        })
    }

    /// Set enabled/disabled state in the WASM module.
    pub fn set_enabled(&mut self, enabled: bool) {
        let func = match self
            .instance
            .get_typed_func::<u32, ()>(&mut self.store, "set_enabled")
        {
            Ok(f) => f,
            Err(e) => {
                tracing::warn!("WASM: set_enabled not found: {}", e);
                return;
            }
        };

        if let Err(e) = func.call(&mut self.store, enabled as u32) {
            tracing::warn!("WASM: set_enabled call failed: {}", e);
        }
    }

    /// Load a deformation field into the WASM module.
    ///
    /// The data must be the raw (decompressed) NPDF binary format.
    pub fn set_deformation_field(&mut self, data: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
        // Allocate memory in WASM for the field data
        let alloc = self
            .instance
            .get_typed_func::<u32, u32>(&mut self.store, "alloc")?;
        let data_ptr = alloc.call(&mut self.store, data.len() as u32)?;

        // Write data into WASM memory
        self.memory
            .write(&mut self.store, data_ptr as usize, data)?;

        // Call set_deformation_field(data_ptr, data_len) -> result
        let set_field = self.instance.get_typed_func::<(u32, u32), u32>(
            &mut self.store,
            "set_deformation_field",
        )?;
        let result = set_field.call(&mut self.store, (data_ptr, data.len() as u32))?;

        // Free the input allocation
        let dealloc = self
            .instance
            .get_typed_func::<(u32, u32), ()>(&mut self.store, "dealloc")?;
        let _ = dealloc.call(&mut self.store, (data_ptr, data.len() as u32));

        if result != 0 {
            return Err("WASM: set_deformation_field returned error".into());
        }

        Ok(())
    }

    /// Call the WASM module to process a layer's paths.
    ///
    /// Input: serialized protobuf bytes (ModifyRequest)
    /// Output: serialized protobuf bytes (ModifyResponse)
    pub fn process_layer(&mut self, input: &[u8]) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        let alloc = self
            .instance
            .get_typed_func::<u32, u32>(&mut self.store, "alloc")?;
        let input_ptr = alloc.call(&mut self.store, input.len() as u32)?;

        self.memory
            .write(&mut self.store, input_ptr as usize, input)?;

        let output_len_ptr = alloc.call(&mut self.store, 4)?;

        let process = self
            .instance
            .get_typed_func::<(u32, u32, u32), u32>(&mut self.store, "process_layer")?;
        let output_ptr = process.call(
            &mut self.store,
            (input_ptr, input.len() as u32, output_len_ptr),
        )?;

        if output_ptr == 0 {
            return Err("WASM process_layer returned null".into());
        }

        let mut len_bytes = [0u8; 4];
        self.memory
            .read(&self.store, output_len_ptr as usize, &mut len_bytes)?;
        let output_len = u32::from_le_bytes(len_bytes) as usize;

        let mut output = vec![0u8; output_len];
        self.memory
            .read(&self.store, output_ptr as usize, &mut output)?;

        let dealloc = self
            .instance
            .get_typed_func::<(u32, u32), ()>(&mut self.store, "dealloc")?;
        let _ = dealloc.call(&mut self.store, (input_ptr, input.len() as u32));
        let _ = dealloc.call(&mut self.store, (output_ptr, output_len as u32));
        let _ = dealloc.call(&mut self.store, (output_len_ptr, 4));

        Ok(output)
    }
}

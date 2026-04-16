---
name: vnc-gui-testing
description: >
  Set up Docker containers with VNC for AI-agent-driven GUI testing of desktop applications.
  Use when testing desktop/GUI apps (Qt, GTK, Electron) via Docker containers that an AI agent
  can interact with through screenshots and mouse/keyboard input. Covers linuxserver.io base
  images, x11vnc as s6 service, mcp-vnc MCP integration, software rendering, Apple Silicon
  workarounds, and pre-seeded config to suppress dialogs. Also use when encountering Rosetta
  cache permission issues, volume mount shadowing, or KasmVNC + raw VNC dual-access patterns.
---

# VNC GUI Testing for AI Agents

Patterns and battle-tested configuration for running desktop applications in Docker containers
that AI coding agents can interact with via VNC screenshots and input.

> **Origin**: Developed while building a Docker-based testing harness for the BrickLayers
> CuraEngine plugin. Every pattern here was discovered through real debugging sessions.

## Architecture Overview

```
+------------------+     +-----------------------------+
|  AI Agent (host) |     |  Docker Container           |
|                  |     |  (linuxserver base image)   |
|  mcp-vnc MCP  --------->  x11vnc :5900 (raw VNC)    |
|  (screenshots,   |     |       |                     |
|   click, type)   |     |  Xorg :1 --> App (Cura/Qt)  |
|                  |     |       |                     |
|  Browser      --------->  KasmVNC :3000 (web UI)     |
|  (human debug)   |     |                             |
+------------------+     +-----------------------------+
```

**Dual-access pattern:**
- **Port 3000** — KasmVNC browser UI for human visual debugging (built into linuxserver images)
- **Port 5900** — Raw VNC protocol for `mcp-vnc` AI agent automation

## Quick Start

### 1. Dockerfile (extend linuxserver base)

```dockerfile
FROM lscr.io/linuxserver/<app>:latest

# Install x11vnc for raw VNC access (KasmVNC on :3000 is browser-only)
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends x11vnc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Fix Rosetta CDI cache ownership (Apple Silicon + Colima)
RUN mkdir -p /custom-cont-init.d \
    && printf '#!/bin/bash\nchown -R abc:abc /config/.cache 2>/dev/null || true\n' \
       > /custom-cont-init.d/fix-cache-perms.sh \
    && chmod +x /custom-cont-init.d/fix-cache-perms.sh

# s6 service: x11vnc on port 5900, sharing the existing X display
RUN mkdir -p /etc/s6-overlay/s6-rc.d/x11vnc/dependencies.d \
    && echo "longrun" > /etc/s6-overlay/s6-rc.d/x11vnc/type \
    && printf '#!/bin/bash\n\n# Wait for X server to be ready\nwhile ! xdpyinfo -display :1 >/dev/null 2>&1; do sleep 1; done\nexec x11vnc -display :1 -forever -shared -nopw -rfbport 5900 -noxdamage -noshm\n' \
       > /etc/s6-overlay/s6-rc.d/x11vnc/run \
    && chmod +x /etc/s6-overlay/s6-rc.d/x11vnc/run \
    && touch /etc/s6-overlay/s6-rc.d/x11vnc/dependencies.d/svc-xorg \
    && touch /etc/s6-overlay/s6-rc.d/user/contents.d/x11vnc
```

### 2. docker-compose.yml

```yaml
services:
  app:
    build:
      context: ./docker
      dockerfile: Dockerfile
    platform: linux/amd64
    container_name: my-app-test
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=UTC
      # Software rendering (required without GPU)
      - LIBGL_ALWAYS_SOFTWARE=1
      - GALLIUM_DRIVER=llvmpipe
    volumes:
      # Named volume for persistent config
      - app-data:/config
      # Bind-mount plugin/app sources (more specific path wins over named volume)
      - ./src:/config/.local/share/app/plugins/MyPlugin
    ports:
      - "3000:3000"   # KasmVNC browser access
      - "5900:5900"   # Raw VNC for mcp-vnc
    restart: unless-stopped

volumes:
  app-data:
```

### 3. MCP Configuration (`.mcp.json`)

```json
{
  "mcpServers": {
    "vnc": {
      "command": "npx",
      "args": ["-y", "@hrrrsn/mcp-vnc"],
      "env": {
        "VNC_HOST": "localhost",
        "VNC_PORT": "5900",
        "VNC_PASSWORD": ""
      }
    }
  }
}
```

### 4. Start and interact

```bash
docker compose up -d          # Start container
# Wait 20-40s for app to initialize
# AI agent uses mcp-vnc tools: vnc_screenshot, vnc_click, vnc_type_text, vnc_key_press
```

## Critical Lessons Learned

### 1. s6 Service Ordering for x11vnc

x11vnc MUST wait for the X server. Without this, it crashes on startup.

**Required s6 structure:**
```
/etc/s6-overlay/s6-rc.d/x11vnc/
  type                        # Contains: "longrun"
  run                         # Startup script (see below)
  dependencies.d/
    svc-xorg                  # Declares dependency on X server service

/etc/s6-overlay/s6-rc.d/user/contents.d/
  x11vnc                      # Registers x11vnc in the default bundle
```

**The `run` script must poll for X:**
```bash
#!/bin/bash
# Wait for X server to be ready
while ! xdpyinfo -display :1 >/dev/null 2>&1; do sleep 1; done
exec x11vnc -display :1 -forever -shared -nopw -rfbport 5900 -noxdamage -noshm
```

The `dependencies.d/svc-xorg` file tells s6 to start x11vnc only after `svc-xorg` is up,
but the poll loop is still necessary because "service up" doesn't mean "X is accepting
connections yet."

### 2. x11vnc Flags Explained

| Flag | Why |
|------|-----|
| `-display :1` | linuxserver images run Xorg on display :1 (not :0) |
| `-forever` | Don't exit after first client disconnects |
| `-shared` | Allow multiple simultaneous VNC connections |
| `-nopw` | No password (container-local, not exposed to internet) |
| `-rfbport 5900` | Standard VNC port |
| `-noxdamage` | Disable X DAMAGE extension (avoids rendering artifacts in containers) |
| `-noshm` | **Critical in Docker** — disable MIT-SHM. Shared memory doesn't work across container boundary, causes segfaults or black screens |

### 3. Volume Mount Shadowing

Docker volume mounts follow "most specific path wins":

```yaml
volumes:
  # Named volume owns /config
  - app-data:/config
  # Bind mount overlays a subdirectory (this WORKS)
  - ./src:/config/.local/share/app/plugins/MyPlugin
```

**Pitfall:** If you mount an entire parent directory via bind mount, it shadows the named volume:
```yaml
# BAD: This shadows the named volume for everything under /config/.local/share/app/
- ./docker/app-config:/config/.local/share/app
# The named volume's content at that path becomes invisible
```

**Fix:** Mount only the specific files you need:
```yaml
# GOOD: Mount individual config file, not the directory
- ./docker/app-config/settings.cfg:/config/.local/share/app/settings.cfg
```

### 4. Rosetta Cache Ownership (Apple Silicon)

On macOS with Apple Silicon, Colima uses Rosetta for x86_64 emulation. Rosetta CDI
creates `/config/.cache` as root before the s6 `init-adduser` script runs, making the
cache directory unwritable by the app user (`abc`).

**Symptom:** App crashes on startup with permission errors writing to `.cache/`.

**Fix:** Custom init script that runs before the app:
```bash
#!/bin/bash
chown -R abc:abc /config/.cache 2>/dev/null || true
mkdir -p /config/.cache/app/version
chown -R abc:abc /config/.cache/app
```

Place in `/custom-cont-init.d/fix-cache-perms.sh` (linuxserver pattern) or as an
s6 init script.

### 5. Software Rendering

GUI apps using OpenGL (Qt, GTK with hardware acceleration) need software rendering
in containers without GPU passthrough:

```yaml
environment:
  - LIBGL_ALWAYS_SOFTWARE=1    # Force Mesa software rasterizer
  - GALLIUM_DRIVER=llvmpipe    # Use LLVM-based software pipeline
```

The linuxserver base images include `libgl1-mesa-dri` for this. Without these env vars,
the app either crashes with "no OpenGL context" or renders a black screen.

### 6. Pre-Seeded Configuration

Desktop apps often show first-run dialogs (EULA, telemetry consent, update prompts)
that block AI agent automation. Pre-seed the config to suppress them:

```ini
# Example: Cura config to disable all blocking dialogs
[general]
agreed_to_send_telemetry = False
check_updates_on_start = False
crash_report = False
send_anonymized_data = False

[notifications]
update_available = False
```

Mount via bind mount:
```yaml
- ./docker/app-config/settings.cfg:/config/.local/share/app/settings.cfg
```

### 7. Apple Silicon: Colima Configuration

linuxserver images are typically amd64-only. On Apple Silicon:

```bash
# Start Colima with x86_64 emulation
colima start --arch x86_64 --vm-type vz

# Or if using krunkit (check your colima config)
# Rosetta emulation is automatic
```

The `platform: linux/amd64` in docker-compose.yml tells Docker to use emulation.

## Testing Architecture (Recommended Tiers)

### Tier 1: Mock/Unit Tests (No Docker)

Test application logic directly with mocked interfaces. Fast, runs in CI.

```bash
python -m pytest tests/ -v
```

### Tier 2: Visual Verification via VNC (Docker)

AI agent drives the GUI through VNC to verify visual behavior:

1. Take screenshot via `vnc_screenshot`
2. Identify UI elements in the screenshot
3. Click/type via `vnc_click`, `vnc_type_text`
4. Take another screenshot to verify result

**AI agent workflow:**
```
1. vnc_screenshot → see app state
2. vnc_click(x, y) → click "Open File" button
3. vnc_type_text("/path/to/test.stl") → enter filename
4. vnc_key_press("Return") → confirm
5. vnc_screenshot → verify file loaded
6. Repeat for remaining test steps
```

### Tier 3: Full CI Pipeline

Automated build + test + package in CI. May include both Tier 1 and Tier 2.

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| "Failed to reconnect to VNC" | Container still starting | Wait 20-40s after `docker compose up`, or add a health check |
| Black screen in VNC | Missing software rendering env vars | Add `LIBGL_ALWAYS_SOFTWARE=1` and `GALLIUM_DRIVER=llvmpipe` |
| App crash on startup | Rosetta cache permissions | Add `fix-cache-perms.sh` init script |
| x11vnc exits immediately | X server not ready | Ensure poll loop in `run` script and `svc-xorg` dependency |
| Volume mount conflicts | Parent mount shadows child | Mount specific files, not parent directories |
| "No protocol specified" x11vnc error | XAUTHORITY not set | linuxserver images handle this; ensure running as service, not manual |
| App shows first-run dialog | Config not pre-seeded | Mount pre-configured settings file |
| Segfault in x11vnc | Missing `-noshm` flag | Always use `-noshm` in Docker containers |
| Slow rendering | Software rendering overhead | Expected; llvmpipe is ~10x slower than GPU. Acceptable for testing |
| Container OOM killed | App + rendering too large | Increase Docker memory limit: `deploy.resources.limits.memory: 4g` |

## Reference: linuxserver.io s6 Service Anatomy

```
/etc/s6-overlay/s6-rc.d/
  <service-name>/
    type              # "longrun" | "oneshot"
    run               # Executable: startup script (longrun)
    up                # Executable: run once (oneshot)
    dependencies.d/   # Touch files named after dependencies
      svc-xorg        # Wait for X server
      svc-kasmvnc     # Wait for KasmVNC (if needed)

/etc/s6-overlay/s6-rc.d/user/contents.d/
  <service-name>      # Touch file to register in default bundle

/custom-cont-init.d/  # Scripts run during container init (linuxserver pattern)
  fix-perms.sh        # Fix file permissions
  install-extras.sh   # Install additional packages
```

## Reference Files

- `references/docker-compose-reference.yml` — Full annotated docker-compose.yml
- `references/dockerfile-reference` — Full annotated Dockerfile
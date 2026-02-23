# Ultimaker Digital Factory API Integration in Cura

This document describes how Cura integrates with the Ultimaker Digital Factory cloud
platform. It covers authentication, API endpoints, cloud printing, the Digital Library,
material syncing, and whether Cura can be used from the command line to submit print jobs.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Authentication & OAuth2](#authentication--oauth2)
3. [Cloud Constants & Base URLs](#cloud-constants--base-urls)
4. [Cloud Printing (Connect API)](#cloud-printing-connect-api)
5. [Digital Library (Cura API)](#digital-library-cura-api)
6. [Material Sync](#material-sync)
7. [Marketplace / Packages API](#marketplace--packages-api)
8. [Polling & Real-Time Updates](#polling--real-time-updates)
9. [Permissions Model](#permissions-model)
10. [Data Models Reference](#data-models-reference)
11. [CLI / Headless Usage & Submitting G-code Programmatically](#cli--headless-usage--submitting-g-code-programmatically)
12. [Key Source Files](#key-source-files)

---

## Architecture Overview

Cura communicates with three major Ultimaker cloud service areas, all hosted under
`https://api.ultimaker.com`:

```
                    ┌─────────────────────────────────────────────┐
                    │           Ultimaker Cloud Platform           │
                    │                                             │
                    │  account.ultimaker.com   (OAuth2 / IdP)     │
                    │  api.ultimaker.com       (REST APIs)        │
                    │  digitalfactory.ultimaker.com (Web UI)      │
                    └──────────┬──────────┬──────────┬────────────┘
                               │          │          │
                    ┌──────────┴──┐ ┌─────┴────┐ ┌──┴───────────┐
                    │ Connect API │ │ Cura API │ │ Packages API │
                    │ /connect/v1 │ │ /cura/v1 │ │/cura-packages│
                    └──────┬──────┘ └────┬─────┘ └──────┬───────┘
                           │             │              │
              ┌────────────┴───┐   ┌─────┴──────┐  ┌───┴────────────┐
              │CloudApiClient  │   │DF ApiClient│  │Marketplace     │
              │CloudOutputDev  │   │DFController│  │PackageList     │
              │CloudOutputMgr  │   │DFFileUpload│  │PackageModel    │
              └────────────────┘   └────────────┘  └────────────────┘
```

All API requests are authenticated via OAuth2 Bearer tokens. The token is injected by
`UltimakerCloudScope` (see `cura/UltimakerCloud/UltimakerCloudScope.py`).

---

## Authentication & OAuth2

### OAuth2 Configuration

| Setting               | Value                                                       |
|-----------------------|-------------------------------------------------------------|
| OAuth Server          | `https://account.ultimaker.com`                             |
| Authorization URL     | `https://account.ultimaker.com/authorize`                   |
| Token URL             | `https://account.ultimaker.com/token`                       |
| Check-Token URL       | `https://account.ultimaker.com/check-token`                 |
| Permissions URL       | `https://account.ultimaker.com/users/permissions`           |
| Client ID             | `um----------------------------ultimaker_cura`              |
| Callback Port         | `32118` (localhost)                                         |
| Callback URL          | `http://localhost:32118/callback`                           |
| PKCE                  | Yes (S512 code challenge)                                   |
| Auth Data Storage Key | `general/ultimaker_auth_data` (in Cura preferences)         |

**Source:** `cura/API/Account.py:95-107`

### OAuth2 Scopes

```
account.user.read
drive.backup.read
drive.backup.write
packages.download
packages.rating.read
packages.rating.write
connect.cluster.read
connect.cluster.write
connect.material.write
library.project.read
library.project.write
cura.printjob.read
cura.printjob.write
cura.mesh.read
cura.mesh.write
```

**Source:** `cura/API/Account.py:74-77`

### Authentication Flow

1. **Login initiated:** `Account.login()` calls `AuthorizationService.startAuthorizationFlow()`.
2. **PKCE challenge:** A verification code and SHA-512 challenge are generated.
3. **Local server started:** `LocalAuthorizationServer` listens on `localhost:32118`.
4. **Browser opened:** The user is redirected to `account.ultimaker.com/authorize` with
   `response_type=code`, `code_challenge`, `code_challenge_method=S512`, and the OAuth2
   `client_id` and `scope`.
5. **Callback received:** The local server receives the authorization code at `/callback`.
6. **Token exchange:** `AuthorizationHelpers.getAccessTokenUsingAuthorizationCode()` POSTs to
   `account.ultimaker.com/token` with `grant_type=authorization_code`.
7. **Token stored:** The `AuthenticationResponse` (access_token, refresh_token, expires_in) is
   stored in Cura preferences. Tokens are stored in the OS keyring when available (Windows
   Credential Manager, macOS Keychain). On Linux, keyring is disabled and tokens are stored
   in the preferences file.
8. **Token refresh:** `AuthorizationService.refreshAccessToken()` POSTs to the token endpoint
   with `grant_type=refresh_token`. Up to 15 retries at 1-second intervals.
9. **Token injection:** `UltimakerCloudScope.requestHook()` adds
   `Authorization: Bearer <token>` to every cloud API request.

**Source:** `cura/OAuth2/AuthorizationService.py`, `cura/OAuth2/AuthorizationHelpers.py`

### Token Validation

Cura validates tokens by calling:
```
GET https://account.ultimaker.com/check-token
Authorization: Bearer <access_token>
```

The response contains user profile data: `user_id`, `username`, `profile_image_url`,
`organization.organization_id`, and `subscriptions`.

**Source:** `cura/OAuth2/AuthorizationHelpers.py:111-170`

---

## Cloud Constants & Base URLs

Defined in `cura/UltimakerCloud/UltimakerCloudConstants.py`:

| Constant                       | Default Value                                |
|--------------------------------|----------------------------------------------|
| `DEFAULT_CLOUD_API_ROOT`       | `https://api.ultimaker.com`                  |
| `DEFAULT_CLOUD_ACCOUNT_API_ROOT` | `https://account.ultimaker.com`           |
| `DEFAULT_DIGITAL_FACTORY_URL`  | `https://digitalfactory.ultimaker.com`       |
| `DEFAULT_CLOUD_API_VERSION`    | `1`                                          |

These can be overridden at build time via `cura.CuraVersion` module attributes
(`CuraCloudAPIRoot`, `CuraCloudAccountAPIRoot`, `CuraDigitalFactoryURL`).

### Metadata Keys

| Key                          | Description                                         |
|------------------------------|-----------------------------------------------------|
| `um_linked_to_account`       | Whether a cloud printer is linked to an account     |
| `capabilities`               | Comma-separated list of printer capabilities        |
| `um_cloud_cluster_id`        | Cloud cluster ID stored in machine stack metadata   |
| `host_guid`                  | Hardware GUID of the printer                        |
| `um_network_key`             | Network discovery key (hostname-based)              |

---

## Cloud Printing (Connect API)

The Connect API manages cloud-connected printer clusters. It is used for printer discovery,
status polling, and print job submission.

**API Client:** `plugins/UM3NetworkPrinting/src/Cloud/CloudApiClient.py`
**Base URL:** `https://api.ultimaker.com/connect/v1`

### Endpoints

#### 1. List Clusters (Printer Discovery)

```
GET /connect/v1/clusters?status=active
Authorization: Bearer <token>
```

**Response model:** `List[CloudClusterResponse]`

Returns all active clusters for the authenticated user. Each cluster represents a printer
or group of printers.

**Used by:** `CloudOutputDeviceManager._getRemoteClusters()`
**Source:** `CloudApiClient.py:73-84`

#### 2. List Clusters by Machine Type

```
GET /connect/v1/clusters?machine_variant={machine_type}
Authorization: Bearer <token>
```

**Response model:** `List[CloudClusterWithConfigResponse]`

Used when multiple cloud printers of the same type exist and the user needs to select one.
The machine type name requires conversion from internal ID format (e.g. `ultimaker_s5`) to
display format (e.g. `Ultimaker S5`). A mapping file `machine_id_to_name.json` is used.

**Used by:** `AbstractCloudOutputDevice` (printer selection dialog)
**Source:** `CloudApiClient.py:86-107`

#### 3. Get Cluster Status (Polling)

```
GET /connect/v1/clusters/{cluster_id}/status
Authorization: Bearer <token>
```

**Response model:** `CloudClusterStatus` containing:
- `printers: List[ClusterPrinterStatus]`
- `print_jobs: List[ClusterPrintJobStatus]`

Called every 10 seconds per connected cloud printer. Returns real-time printer and print
job status.

**Used by:** `CloudOutputDevice._update()`
**Source:** `CloudApiClient.py:109-120`

#### 4. Register Print Job Upload

```
PUT /cura/v1/jobs/upload
Authorization: Bearer <token>
Content-Type: application/json

{
    "data": {
        "job_name": "my_print",
        "file_size": 1048576,
        "content_type": "application/gzip"
    }
}
```

**Response model:** `CloudPrintJobResponse` containing:
- `job_id` - Unique job identifier
- `upload_url` - Signed URL for uploading the actual file data
- `content_type` - MIME type to use when uploading
- `status` - Current job status

Note: This endpoint is under `/cura/v1`, not `/connect/v1`.

**Source:** `CloudApiClient.py:122-138`

#### 5. Upload Toolpath / G-code

```
PUT {upload_url}
Content-Type: {content_type from step 4}
Body: <raw binary data>
```

The `upload_url` is a signed URL returned by the upload registration endpoint. The upload
is handled by `ToolPathUploader` which supports:
- Progress callbacks
- Retry on HTTP 500/502/503/504 (up to 10 retries)
- Cancellation

**Source:** `plugins/UM3NetworkPrinting/src/Cloud/ToolPathUploader.py`

#### 6. Request Print (Submit to Printer Queue)

```
POST /connect/v1/clusters/{cluster_id}/print/{job_id}
Authorization: Bearer <token>
Body: (empty)
```

**Response model:** `CloudPrintResponse`

Tells the cloud to send the uploaded job to the specified printer cluster. If the response
is empty/falsy, the job requires approval (shown via `PrintJobPendingApprovalMessage`).

**Error handling:**
- HTTP 409 with code `printerInactive` -> printer is offline/inactive
- HTTP 409 other codes -> print queue is full

**Source:** `CloudApiClient.py:162-169`, `CloudOutputDevice.py:292-331`

#### 7. Print Job Actions

```
POST /connect/v1/clusters/{cluster_id}/print_jobs/{job_id}/action/{action}
Authorization: Bearer <token>
Content-Type: application/json

Body: {"data": <optional action data>}  (or empty)
```

**Supported actions:**

| Action   | Data                                | Description                |
|----------|-------------------------------------|----------------------------|
| `move`   | `{"list": "queued", "to_position": 0}` | Move job to top of queue |
| `remove` | (none)                              | Remove job from queue      |
| `force`  | (none)                              | Force-send despite config mismatch |

**Source:** `CloudApiClient.py:171-193`, `CloudOutputDevice.py:393-409`

#### 8. Material Upload

```
PUT /connect/v1/materials/upload
Authorization: Bearer <token>
Content-Type: application/json

{
    "data": {
        "file_size": 123456,
        "material_profile_name": "cura.umm",
        "content_type": "application/zip",
        "origin": "cura"
    }
}
```

Returns an `upload_url` and `material_profile_id`. After uploading the .zip archive to
the signed URL:

```
POST /connect/v1/clusters/{cluster_id}/printers/{printer_id}/action/import_material
Authorization: Bearer <token>

{"data": {"material_profile_id": "<id>"}}
```

**Source:** `cura/PrinterOutput/UploadMaterialsJob.py:49-50`

### Complete Print Job Upload Flow

```
User clicks "Print via cloud"
        │
        ▼
CloudOutputDevice.requestWrite(nodes)
        │
        ├─ If mesh already uploaded → requestPrint() directly (reprint)
        │
        ▼
ExportFileJob (exports scene to .gcode/.ufp/.makerbot)
        │
        ▼
CloudApiClient.requestUpload(job_name, file_size, content_type)
    PUT /cura/v1/jobs/upload
        │
        ▼ Returns upload_url + job_id
ToolPathUploader.start()
    PUT {upload_url}  (binary data, with progress + retry)
        │
        ▼
CloudApiClient.requestPrint(cluster_id, job_id)
    POST /connect/v1/clusters/{cluster_id}/print/{job_id}
        │
        ├─ Success → PrintJobUploadSuccessMessage
        ├─ HTTP 409 (printerInactive) → PrintJobUploadPrinterInactiveMessage
        ├─ HTTP 409 (other) → PrintJobUploadQueueFullMessage
        └─ wait_approval status → PrintJobPendingApprovalMessage
```

---

## Digital Library (Cura API)

The Digital Library allows users to save and load project files to/from their Ultimaker
account.

**API Client:** `plugins/DigitalLibrary/src/DigitalFactoryApiClient.py`
**Base URL:** `https://api.ultimaker.com/cura/v1`

### Endpoints

#### 1. Check User Access (Feature Budgets)

```
GET /cura/v1/feature_budgets
Authorization: Bearer <token>
```

**Response model:** `DigitalFactoryFeatureBudgetResponse`

Checks if `library_max_private_projects` is > 0 or == -1 (unlimited).

**Source:** `DigitalFactoryApiClient.py:61-81`

#### 2. List Projects

```
GET /cura/v1/projects[?limit=N&search=<filter>]
Authorization: Bearer <token>
```

**Response model:** `List[DigitalFactoryProjectResponse]`

Supports pagination via `links.next_page` in the response. The `PaginationManager` tracks
the current page state.

**Source:** `DigitalFactoryApiClient.py:134-186`

#### 3. Get Single Project

```
GET /cura/v1/projects/{library_project_id}
Authorization: Bearer <token>
```

**Source:** `DigitalFactoryApiClient.py:118-132`

#### 4. Create Project

```
PUT /cura/v1/projects
Authorization: Bearer <token>
Content-Type: application/json

{"data": {"display_name": "My Project"}}
```

**Source:** `DigitalFactoryApiClient.py:365-381`

#### 5. List Files in Project

```
GET /cura/v1/projects/{library_project_id}/files
Authorization: Bearer <token>
```

**Response model:** `List[DigitalFactoryFileResponse]`

**Source:** `DigitalFactoryApiClient.py:188-201`

#### 6. Request 3MF File Upload

```
PUT /cura/v1/files/upload
Authorization: Bearer <token>
Content-Type: application/json

{
    "data": {
        "content_type": "application/x-cura-project+3mf",
        "file_name": "my_project.3mf",
        "file_size": 2097152,
        "library_project_id": "<project_id>"
    }
}
```

**Response model:** `DFLibraryFileUploadResponse` (includes `upload_url`, `file_id`)

**Source:** `DigitalFactoryApiClient.py:299-318`

#### 7. Request Print Job File Upload (UFP/Makerbot)

```
PUT /cura/v1/jobs/upload
Authorization: Bearer <token>
Content-Type: application/json

{
    "data": {
        "content_type": "application/x-ufp",
        "job_name": "my_print.ufp",
        "file_size": 4194304,
        "library_project_id": "<project_id>",
        "source_file_id": "<3mf_file_id>"
    }
}
```

**Response model:** `DFPrintJobUploadResponse` (includes `upload_url`, `job_name`)

The `source_file_id` links the print file to its source 3MF project file.

**Source:** `DigitalFactoryApiClient.py:320-338`

#### 8. Upload File Data

```
PUT {upload_url}
Content-Type: {content_type}
Body: <raw binary data>
```

Handled by `DFFileUploader`, similar to `ToolPathUploader`.

**Source:** `DigitalFactoryApiClient.py:340-363`

### Digital Library Upload Flow

```
User clicks "Save to Digital Library"
        │
        ▼
DFFileExportAndUploadManager.start()
        │
        ├── ExportFileJob (3MF) ──► requestUpload3MF() ──► uploadFileData()
        │                              PUT /cura/v1/files/upload
        │                              returns file_id (used as source_file_id)
        │
        └── ExportFileJob (UFP/Makerbot) ──► requestUploadMeshFile() ──► uploadFileData()
                                                PUT /cura/v1/jobs/upload
                                                includes source_file_id from 3MF

Both uploads track progress via _message_lock (thread-safe)
Success → "Upload successful" message with "Open project" link
```

---

## Material Sync

Cloud material syncing uploads all local material profiles as a .zip archive and
distributes them to online cloud printers.

**Source:** `cura/UltimakerCloud/CloudMaterialSync.py`, `cura/PrinterOutput/UploadMaterialsJob.py`

### Flow

1. `CloudMaterialSync.exportUpload()` creates an `UploadMaterialsJob`.
2. The job exports all material profiles to a temporary `.umm` (zip) archive.
3. Requests upload registration:
   ```
   PUT /connect/v1/materials/upload
   {"data": {"file_size": N, "material_profile_name": "cura.umm",
             "content_type": "application/zip", "origin": "cura"}}
   ```
4. Uploads the archive to the returned `upload_url`.
5. For each online cloud printer with `import_material` capability:
   ```
   POST /connect/v1/clusters/{cluster_id}/printers/{printer_id}/action/import_material
   {"data": {"material_profile_id": "<id>"}}
   ```
6. Tracks per-printer sync status (uploading/success/failed).

### Requirements for Printer Material Sync

- Printer must be a cloud printer (connection_type = 3)
- Printer must be online (`is_online = True`)
- Printer must have `host_guid` and `um_cloud_cluster_id` metadata
- Printer must have `import_material` in its capabilities (requires firmware >= 7.0.1-RC)

---

## Marketplace / Packages API

**Source:** `plugins/Marketplace/Constants.py`

```
ROOT_URL = https://api.ultimaker.com/cura-packages/v1
ROOT_CURA_URL = https://api.ultimaker.com/cura-packages/v1/cura/v{CuraSDKVersion}
```

| Endpoint                  | URL Pattern                              |
|---------------------------|------------------------------------------|
| List packages             | `GET {ROOT_CURA_URL}/packages`           |
| Package updates           | `GET {ROOT_CURA_URL}/packages/package-updates` |
| User packages             | `GET {ROOT_URL}/user/packages`           |

---

## Polling & Real-Time Updates

Cura uses **HTTP polling** (not WebSockets) for all real-time updates.

| Component                    | Interval   | What it polls                              |
|------------------------------|------------|--------------------------------------------|
| `CloudOutputDevice._update()`| 10 seconds | Cluster status (printers + print jobs)     |
| `AbstractCloudOutputDevice`  | 10 seconds | Cluster configurations for printer type    |
| `Account._update_timer`      | 60 seconds | Triggers `syncRequested` signal for all sync services |

### Offline Detection

If no response is received within `NETWORK_RESPONSE_CONSIDER_OFFLINE = 15.0` seconds, the
cloud device is considered offline.

**Source:** `CloudOutputDevice.py:57-63`

---

## Permissions Model

After login, Cura fetches the user's permissions from:
```
GET https://account.ultimaker.com/users/permissions
Authorization: Bearer <token>
```

Response: `{"data": {"permissions": ["digital-factory.print-job.read", ...]}}`

### Permission Keys Used in Code

| Permission Key                        | Checked By                          | Purpose                        |
|---------------------------------------|-------------------------------------|--------------------------------|
| `digital-factory.print-job.read`      | `CloudOutputDevice.canReadPrintJobs`       | View print job list     |
| `digital-factory.print-job.write`     | `CloudOutputDevice.canWriteOthersPrintJobs`| Modify others' jobs     |
| `digital-factory.print-job.write.own` | `CloudOutputDevice.canWriteOwnPrintJobs`   | Modify own jobs         |
| `digital-factory.printer.read`        | `CloudOutputDevice.canReadPrinterDetails`  | View printer status     |

**Source:** `CloudOutputDevice.py:421-450`, `Account.py:351-396`

---

## Data Models Reference

### CloudClusterResponse (Cluster Metadata)

| Field               | Type         | Description                               |
|---------------------|------------- |-------------------------------------------|
| `cluster_id`        | `str`        | Unique cluster identifier                 |
| `host_guid`         | `str`        | Hardware GUID                             |
| `host_name`         | `str`        | Network hostname                          |
| `host_version`      | `str`        | Firmware version                          |
| `host_internal_ip`  | `str`        | Local network IP                          |
| `friendly_name`     | `str`        | User-facing name                          |
| `printer_type`      | `str`        | Machine type (e.g. `ultimaker_s5`)        |
| `printer_count`     | `int`        | Number of printers in cluster             |
| `is_online`         | `bool`       | Cloud connectivity status                 |
| `status`            | `str`        | `active` or `inactive`                    |
| `display_status`    | `str`        | Display state                             |
| `capabilities`      | `List[str]`  | e.g. `["queue", "import_material"]`       |

### CloudPrintJobUploadRequest

| Field          | Type  | Description                                      |
|----------------|-------|--------------------------------------------------|
| `job_name`     | `str` | Name of the print job (without extension)        |
| `file_size`    | `int` | Size of the file in bytes                        |
| `content_type` | `str` | MIME type (`application/gzip`, `application/octet-stream`) |

### CloudPrintJobResponse

| Field                | Type           | Description                             |
|----------------------|----------------|-----------------------------------------|
| `job_id`             | `str`          | Unique job ID                           |
| `status`             | `str`          | Job status                              |
| `upload_url`         | `Optional[str]`| Signed URL for uploading (if status=uploading) |
| `download_url`       | `Optional[str]`| Signed URL for downloading result       |
| `job_name`           | `Optional[str]`| Job name                                |
| `content_type`       | `Optional[str]`| MIME type for upload                    |
| `status_description` | `Optional[str]`| Detailed status / failure cause         |

---

## CLI / Headless Usage & Submitting G-code Programmatically

### Does Cura Have a CLI Mode?

**Short answer: Cura has a minimal headless mode, but it does NOT expose cloud API
operations (print job submission, Digital Library upload, etc.) from the command line.**

#### What Exists

1. **`--debug` flag:** Enables debug logging (`cura_app.py:40-48`).
2. **`--single-instance` flag:** Prevents multiple Cura instances.
3. **File arguments:** `cura [file ...]` opens files in the GUI after startup.
4. **Headless mode:** `CuraApplication` inherits a `_is_headless` property from the Uranium
   framework. When set, `runWithoutGUI()` is called instead of `runWithGUI()`. However,
   `runWithoutGUI()` is essentially a no-op -- it only closes the splash screen
   (`CuraApplication.py:985-988`). There is no CLI workflow that uses the cloud APIs.
5. **CuraEngine CLI:** The slicing engine (`CuraEngine`) is a separate C++ binary that can
   be invoked from the command line to slice STL/3MF files into G-code. However, CuraEngine
   has no knowledge of the Ultimaker cloud APIs -- it is purely a local slicer.

#### What Does NOT Exist

- No `--print-to-cloud` or `--upload` CLI command
- No standalone script for submitting G-code to the Digital Factory API
- No REST API server mode that would allow external tools to trigger cloud uploads
- No way to authenticate and use the cloud APIs without the Qt event loop running
  (the `HttpRequestManager`, `QNetworkAccessManager`, and OAuth2 flows all depend on Qt)

### How to Submit G-code to the Digital Factory API Programmatically

Although Cura itself does not provide a CLI for this, you can replicate the API calls
directly. Here is the step-by-step process:

#### Prerequisites

- An Ultimaker account with cloud-connected printers
- A valid OAuth2 access token (obtained via the OAuth2 flow described above)
- The `cluster_id` of the target printer

#### Step-by-Step API Calls

**Step 1: Authenticate and obtain a Bearer token**

Use the OAuth2 Authorization Code flow with PKCE against
`https://account.ultimaker.com/authorize` and `https://account.ultimaker.com/token`.
The client ID is `um----------------------------ultimaker_cura`.

**Step 2: Discover your printer clusters**

```bash
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.ultimaker.com/connect/v1/clusters?status=active"
```

Response:
```json
{
  "data": [
    {
      "cluster_id": "abc123",
      "friendly_name": "My Printer",
      "printer_type": "ultimaker_s5",
      "is_online": true,
      ...
    }
  ]
}
```

**Step 3: Register the print job upload**

```bash
curl -X PUT \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"data": {"job_name": "my_print", "file_size": 1048576, "content_type": "application/gzip"}}' \
     "https://api.ultimaker.com/cura/v1/jobs/upload"
```

Response:
```json
{
  "data": {
    "job_id": "kBEeZWEifXbrXviO8mRYLx45P8k5lHVGs43XKvRniPg=",
    "status": "uploading",
    "upload_url": "https://storage.googleapis.com/...",
    "content_type": "application/gzip"
  }
}
```

Notes on `content_type`:
- `application/gzip` for gzipped G-code (Ultimaker printers)
- `application/octet-stream` for MakerBot format files

**Step 4: Upload the G-code file**

```bash
curl -X PUT \
     -H "Content-Type: application/gzip" \
     --data-binary @my_print.gcode.gz \
     "$UPLOAD_URL"
```

Upload the file to the signed `upload_url` from Step 3. The `Content-Type` header must
match the `content_type` from the response.

The `ToolPathUploader` in Cura retries on HTTP 500/502/503/504 up to 10 times.

**Step 5: Submit the job to the printer**

```bash
curl -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -d '' \
     "https://api.ultimaker.com/connect/v1/clusters/$CLUSTER_ID/print/$JOB_ID"
```

This sends the uploaded job to the printer queue.

**Step 6 (Optional): Monitor status**

```bash
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.ultimaker.com/connect/v1/clusters/$CLUSTER_ID/status"
```

Poll this endpoint to monitor printer and print job status.

#### Considerations for a Custom CLI Tool

1. **OAuth2 token management:** You need to implement the full OAuth2 PKCE flow including
   a local HTTP server callback, or reuse tokens stored by Cura (found in Cura's preferences
   file under `general/ultimaker_auth_data`; access tokens are stored in the OS keyring on
   Windows/macOS, and in the preferences file on Linux).

2. **File format:** Ultimaker printers expect G-code (typically gzipped). MakerBot Method
   printers expect `.makerbot` format files. Cura's `ExportFileJob` handles this conversion,
   but for a standalone tool you would need to provide pre-sliced files.

3. **Content type mapping:** Use `application/gzip` for `.gcode.gz` files and
   `application/octet-stream` for `.makerbot` files.

4. **Error handling:** The cloud API returns errors in the format:
   ```json
   {"errors": [{"id": "...", "code": "...", "title": "...", "detail": "...", "http_status": "409"}]}
   ```

5. **Rate limiting:** Cura polls at 10-second intervals. For a CLI tool, there is no need
   to poll continuously -- just submit the job and optionally check status.

6. **Reusing Cura's stored credentials on Linux:**
   ```python
   import json
   # Cura stores auth data in ~/.local/share/cura/<version>/cura.cfg
   # Under [general], key: ultimaker_auth_data
   # Parse the JSON to get access_token and refresh_token
   ```

#### Example: Minimal Python Script

```python
"""
Minimal example of submitting G-code to Ultimaker Digital Factory.
Requires: requests, a valid access token, and a pre-sliced gcode.gz file.
"""
import gzip
import json
import sys
import requests

API_ROOT = "https://api.ultimaker.com"
TOKEN = "YOUR_ACCESS_TOKEN"  # Obtain via OAuth2 flow
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def list_clusters():
    r = requests.get(f"{API_ROOT}/connect/v1/clusters?status=active", headers=HEADERS)
    r.raise_for_status()
    return r.json()["data"]


def upload_and_print(cluster_id, gcode_path):
    # Read and gzip the file if not already gzipped
    with open(gcode_path, "rb") as f:
        data = f.read()
    if not gcode_path.endswith(".gz"):
        data = gzip.compress(data)

    # Step 1: Register upload
    upload_req = {
        "data": {
            "job_name": gcode_path.rsplit("/", 1)[-1].replace(".gcode.gz", "").replace(".gcode", ""),
            "file_size": len(data),
            "content_type": "application/gzip",
        }
    }
    r = requests.put(
        f"{API_ROOT}/cura/v1/jobs/upload",
        headers={**HEADERS, "Content-Type": "application/json"},
        data=json.dumps(upload_req),
    )
    r.raise_for_status()
    job = r.json()["data"]
    print(f"Job registered: {job['job_id']}")

    # Step 2: Upload the file
    r = requests.put(
        job["upload_url"],
        headers={"Content-Type": job["content_type"]},
        data=data,
    )
    r.raise_for_status()
    print("File uploaded successfully")

    # Step 3: Send to printer
    r = requests.post(
        f"{API_ROOT}/connect/v1/clusters/{cluster_id}/print/{job['job_id']}",
        headers=HEADERS,
    )
    r.raise_for_status()
    print(f"Print job submitted to cluster {cluster_id}")


if __name__ == "__main__":
    clusters = list_clusters()
    for c in clusters:
        status = "ONLINE" if c["is_online"] else "offline"
        print(f"  [{status}] {c['friendly_name']} ({c['cluster_id']})")

    if len(sys.argv) < 3:
        print(f"\nUsage: {sys.argv[0]} <cluster_id> <file.gcode[.gz]>")
        sys.exit(1)

    upload_and_print(sys.argv[1], sys.argv[2])
```

---

## Key Source Files

### Cloud Printing

| File | Description |
|------|-------------|
| `plugins/UM3NetworkPrinting/src/Cloud/CloudApiClient.py` | HTTP client for Connect & Cura APIs |
| `plugins/UM3NetworkPrinting/src/Cloud/CloudOutputDevice.py` | Cloud printer device (status polling, print submission) |
| `plugins/UM3NetworkPrinting/src/Cloud/CloudOutputDeviceManager.py` | Discovery and lifecycle management of cloud printers |
| `plugins/UM3NetworkPrinting/src/Cloud/AbstractCloudOutputDevice.py` | Multi-printer selection when multiple printers of same type exist |
| `plugins/UM3NetworkPrinting/src/Cloud/ToolPathUploader.py` | Binary file upload with retry logic |

### Digital Library

| File | Description |
|------|-------------|
| `plugins/DigitalLibrary/src/DigitalFactoryApiClient.py` | HTTP client for Digital Library API |
| `plugins/DigitalLibrary/src/DFFileExportAndUploadManager.py` | Coordinates parallel 3MF + UFP export and upload |
| `plugins/DigitalLibrary/src/DFFileUploader.py` | File upload handler for Digital Library |
| `plugins/DigitalLibrary/src/DigitalFactoryController.py` | QML-exposed controller for Digital Library UI |

### Authentication

| File | Description |
|------|-------------|
| `cura/API/Account.py` | Account management, OAuth2 settings, permissions, sync timer |
| `cura/OAuth2/AuthorizationService.py` | OAuth2 flow orchestration, token storage and refresh |
| `cura/OAuth2/AuthorizationHelpers.py` | Token exchange, PKCE, JWT validation |
| `cura/OAuth2/LocalAuthorizationServer.py` | Local HTTP server for OAuth2 callback |
| `cura/OAuth2/Models.py` | OAuth2Settings, AuthenticationResponse, UserProfile |
| `cura/OAuth2/KeyringAttribute.py` | Secure token storage via OS keyring |
| `cura/UltimakerCloud/UltimakerCloudScope.py` | Injects Bearer token into API requests |

### Cloud Infrastructure

| File | Description |
|------|-------------|
| `cura/UltimakerCloud/UltimakerCloudConstants.py` | Base URLs and metadata key constants |
| `cura/UltimakerCloud/CloudMaterialSync.py` | Material profile sync to cloud printers |
| `cura/PrinterOutput/UploadMaterialsJob.py` | Material archive upload job |

### Data Models (Connect API)

| File | Description |
|------|-------------|
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudClusterResponse.py` | Cluster metadata |
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudClusterStatus.py` | Real-time status (printers + jobs) |
| `plugins/UM3NetworkPrinting/src/Models/Http/ClusterPrinterStatus.py` | Individual printer state |
| `plugins/UM3NetworkPrinting/src/Models/Http/ClusterPrintJobStatus.py` | Print job state |
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudPrintJobUploadRequest.py` | Upload registration request |
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudPrintJobResponse.py` | Upload registration response |
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudPrintResponse.py` | Print request response |
| `plugins/UM3NetworkPrinting/src/Models/Http/CloudError.py` | API error model |

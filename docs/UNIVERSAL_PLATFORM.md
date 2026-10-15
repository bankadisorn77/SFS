# Universal Platform Core

## Goal

One process runs one PlatformRuntime, and each runtime owns exactly one Project.
The platform core manages lifecycle and shared services; a project provides its
business rules, hardware adapters, optional IO, and communication transports.

## Core contracts

- `ProjectAdapter.create_camera(definition)`: create a camera adapter. The
  core does not import IDS, RTSP, USB, GigE, or any vendor SDK.
- `ProjectAdapter.create_io_manager()`: return an IO adapter or `None`.
- `ProjectAdapter.requires_io()`: whether IO is required for readiness.
- `ProjectAdapter.create_status_reporter()`: select the project's reporting
  transport. The default reporter is transport-free.
- `CameraManager`: connect, read frames, inspect status, reconnect, and
  disconnect adapters without knowing the underlying camera implementation.
- `HealthMonitor`: checks only installed capabilities. No IO means no GPIO
  health field and no GPIO readiness dependency.
- `PlatformRuntime`: owns one project's startup/shutdown, health monitor,
  pipeline engine, storage, and status reporting.

A camera adapter should provide `connection(width, height[, fps])`,
`disconnect()`, `is_connected()`, and `get_latest_frame()`. Optional
capabilities such as `snap_frame`, `reconnect`, `start_live`, and
`stop_live` are detected when present.

## Project-specific behavior

Vendor imports and project-specific transport setup belong in the project
package or application entrypoint. For example, SFS creates its IDS camera
and Advantech IO adapters in its project layer and explicitly injects its
legacy shared-memory reporter from `Program/main.py`. A CCTV project can create
RTSP camera adapters, omit IO, and inject an MQTT command/status adapter without
modifying `platform_core`.

## Reuse

From a checkout of this repository, install the core package with:

```bash
pip install ./Program
```

The core package intentionally declares no vendor SDK dependencies. Projects
must install the drivers and optional libraries required by their own adapters.

## Delivery plan

- Phase 0: remove hardware, IO, project, and transport assumptions from the core.
- Phase 1: adapt the CCTV project to the ProjectRuntime and camera contracts.
- Phase 2: integrate MQTT command and status handling in the CCTV service.
- Phase 3: harden lifecycle, reconnect, idempotency, and integration tests.
- Phase 4: adapt the Web Server; deliberately deferred.

import unittest

from platform_core import CameraManager, HealthMonitor, PlatformRuntime
from platform_core.project import ProjectAdapter


class Reporter:
    def __init__(self):
        self.payloads = []

    def report(self, payload):
        self.payloads.append(dict(payload))

    def close(self):
        pass


class MinimalProject(ProjectAdapter):
    project_id = "minimal"

    def pipeline_definition(self, pipeline_id):
        return {"mode": "shared", "steps": []}

    def get_step_handlers(self):
        return {}

    def run(self, runtime):
        return None


class FakeCamera:
    def __init__(self):
        self.connected = False

    def connection(self, width, height, fps=15):
        self.connected = True
        return True

    def is_connected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def get_latest_frame(self):
        return None


class CameraProject(MinimalProject):
    def camera_definition(self):
        return [{"id": "fake-camera", "enabled": True}]

    def create_camera(self, definition):
        return FakeCamera()


class UniversalRuntimeTests(unittest.TestCase):
    def test_camera_manager_requires_injected_factory(self):
        with self.assertRaises(ValueError):
            CameraManager.from_definitions([{"id": "cam-1"}])

    def test_runtime_without_cameras_or_io_is_ready(self):
        runtime = PlatformRuntime(
            project=MinimalProject(),
            status_reporter=Reporter(),
            health_monitor=HealthMonitor(check_interval=0.1),
        )
        self.assertTrue(runtime.start())
        self.assertTrue(runtime.hardware_ready())
        self.assertNotIn("gpio", runtime.health_monitor.status["details"])
        runtime.stop()

    def test_runtime_uses_project_camera_factory(self):
        runtime = PlatformRuntime(
            project=CameraProject(),
            status_reporter=Reporter(),
            health_monitor=HealthMonitor(check_interval=0.1),
        )
        self.assertTrue(runtime.start())
        self.assertEqual(runtime.camera_manager.ids(), ["fake-camera"])
        runtime.stop()


if __name__ == "__main__":
    unittest.main()

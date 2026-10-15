from abc import ABC, abstractmethod
from typing import Any, Dict, List, Callable

from .context import InspectionContext


class ProjectAdapter(ABC):
    """Project contract consumed by PlatformRuntime.

    Hardware is supplied by the project through factories. The core never imports
    a concrete camera, IO driver, or project implementation.
    """

    @property
    @abstractmethod
    def project_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def pipeline_definition(self, pipeline_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_step_handlers(self) -> Dict[str, Callable[[InspectionContext, Dict[str, Any]], Any]]:
        raise NotImplementedError

    def camera_definition(self) -> List[Dict[str, Any]]:
        return []

    def create_camera(self, definition: Dict[str, Any]):
        """Create a camera adapter for one camera definition.

        Projects must override this when they declare cameras. Keeping this
        factory in the project prevents the platform core from depending on a
        vendor SDK or transport such as IDS, USB, RTSP, or GigE.
        """
        raise NotImplementedError(
            "Projects with cameras must implement create_camera(definition)."
        )

    def requires_io(self) -> bool:
        """Whether IO hardware is mandatory for this project."""
        return False

    def create_io_manager(self):
        """Return an IO adapter, or None when the project has no IO capability."""
        return None

    def io_definition(self) -> Dict[str, Any]:
        return {}

    def storage_definition(self) -> Dict[str, Any]:
        return {}

    def status_payload(self) -> Dict[str, Any]:
        return {}

    def run(self, runtime):
        raise NotImplementedError

    def shutdown(self, runtime):
        """Optional project cleanup hook."""
        return None

    def run_pipeline(self, pipeline_id: str, context: InspectionContext):
        return None

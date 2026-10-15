import logging

from platform_core.runtime import PlatformRuntime
from platform_core.communication import PlatformStatusReporter
from platform_core.module.shm import sender as SharedMemorySender
from projects.SFS.project import SfsPokaYokeProject

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main():
    project = SfsPokaYokeProject()
    reporter = PlatformStatusReporter(sender=SharedMemorySender())
    runtime = PlatformRuntime(project=project, status_reporter=reporter)
    try:
        ready = runtime.start(
            width=project.config.cameraAOI.get("width"),
            height=project.config.cameraAOI.get("height"),
        )
        if not ready:
            logging.getLogger("MAIN").warning(
                "Hardware is not ready; HealthMonitor will keep retrying."
            )
        runtime.run()
    except KeyboardInterrupt:
        logging.getLogger("MAIN").info("Stopping system by user.")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()

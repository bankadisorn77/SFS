import logging

from platform_core.runtime import PlatformRuntime
from projects.SFS.project import SfsPokaYokeProject

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s]: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    project = SfsPokaYokeProject()
    runtime = PlatformRuntime(project=project)
    try:
        ready = runtime.start(
            width=project.config.cameraAOI.get('width'),
            height=project.config.cameraAOI.get('height'),
        )
        if not ready:
            logging.getLogger("MAIN").warning("hardware is not ready, HealthMonitor will keep retrying.")
        runtime.run()
    except KeyboardInterrupt:
        logging.getLogger("MAIN").info("Stopping system by user.")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
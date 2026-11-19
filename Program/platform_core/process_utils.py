"""OS specific process helpers for agent.py (Linux and Windows)."""
import logging
import os
import signal
import subprocess
import sys
import tempfile

logger = logging.getLogger("ProcessUtils")

IS_WINDOWS = sys.platform.startswith("win") or os.name == "nt"


def single_instance_lock(name: str):
    """Return an open lock handle, or None if another instance holds the lock.
    Keep the handle alive for the life of the process."""
    path = os.path.join(tempfile.gettempdir(), f"{name}.lock")
    fd = open(path, "a+")
    try:
        if IS_WINDOWS:
            import msvcrt
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def popen_kwargs() -> dict:
    """Start the worker in its own process group, without a console window on Windows."""
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {"start_new_session": True}


def stop_steps(timeout: float):
    """(signal or None, wait seconds) tried in order to stop the worker.
    None = only wait (the worker saw the stop flag and exits cleanly)."""
    if IS_WINDOWS:
        return [
            (None, timeout),
            (getattr(signal, "CTRL_BREAK_EVENT", None), 5),
            ("terminate", 3),
            ("kill", 2),
        ]
    return [
        (None, timeout),
        (signal.SIGINT, 5),
        (signal.SIGTERM, 3),
        (signal.SIGKILL, 2),
    ]


def send_step(proc, step):
    """Apply one stop step to a psutil.Process."""
    if step is None:
        return
    if step == "terminate":
        proc.terminate()
    elif step == "kill":
        proc.kill()
    else:
        proc.send_signal(step)


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a or "")) == os.path.normcase(os.path.realpath(b or ""))

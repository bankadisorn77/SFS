"""Force every digital output OFF after the inspection worker has stopped.

The USB-4761 keeps the last written value when the process that wrote it dies.
If the worker crashes (or is killed) right after a PASS, the PASS / relay /
ready outputs would stay ON and the machine could run without inspection.
agent.py calls reset_outputs() once the worker process is gone.
"""
import logging
from typing import Iterable, Optional

logger = logging.getLogger("SafeOutputs")


def reset_outputs(channels: Iterable[int], device: str = "USB-4761,BID#0",
                  io_manager=None) -> Optional[bool]:
    """Write False to every channel. Returns True/False, or None if nothing to do.

    Must only be called while the worker is NOT running (it opens the card itself).
    """
    chans = sorted({int(c) for c in channels or [] if c is not None})
    if not chans:
        return None
    own = io_manager is None
    try:
        if own:
            from .io_manager import AdvantechIOManager
            io_manager = AdvantechIOManager(device=device)
        if not io_manager.connect(max_retries=2, retry_delay=0.5):
            logger.error("Cannot open IO card %s to reset outputs %s.", device, chans)
            return False
        ok = True
        for ch in chans:
            ok = bool(io_manager.write_output(ch, False)) and ok
        if ok:
            logger.info("All outputs reset to OFF: %s", chans)
        else:
            logger.error("Some outputs could not be reset: %s", chans)
        return ok
    except Exception as exc:
        logger.error("Output reset failed: %s", exc)
        return False
    finally:
        if own and io_manager is not None:
            try:
                io_manager.close()
            except Exception:
                pass

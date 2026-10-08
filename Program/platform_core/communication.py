# platform_core/communication.py
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("Communication")

from .module.shm import sender as _SharedMemorySender


class PlatformStatusReporter:

  def __init__(self):
    self.sender = None
    if _SharedMemorySender is None:
      logger.warning("Shared memory sender module is not available.")
      return

    try:
      self.sender = _SharedMemorySender()
      self.sender.connect()
    except Exception as e:
      logger.error(f"Shared memory sender initialization failed: {e}")
      self.sender = None

  def report(self, payload=None):
    if self.sender and payload is not None:
      try:
        data = dict(payload)
        data["last_update"] = time.time()
        self.sender.send_event(data)
      except Exception as exc:
        logger.warning("Status report failed: %s", exc)

  def close(self):
    if self.sender:
      try:
        self.sender.close()
      except Exception:
        pass
      self.sender = None
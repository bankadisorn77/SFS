import asyncio
import json
import os
import logging
from multiprocessing import shared_memory
import struct
import time

SHM_NAME = "PROJECT_SHARED_STATUS_MEMORY"
SHM_SIZE = 8192 
SHM_CHECK_INTERVAL_S = 2.0
logger = logging.getLogger(__name__)


def _shm_inode(name):
  """Inode of the named segment on Linux (/dev/shm), or None if unavailable."""
  if os.name != "posix":
    return None
  try:
    return os.stat(os.path.join("/dev/shm", name.lstrip("/"))).st_ino
  except OSError:
    return None


class resiver:

  def __init__(self, on_data_received=None):
    self.running = True
    self.on_data_received = on_data_received
    self.shm = None
    self.last_seq = -1  
    self._inode = None
    self._last_check = 0.0

  def _attach_shm(self):
    if self.shm is not None:
      return True
    try:
      self.shm = shared_memory.SharedMemory(name=SHM_NAME)
      self._inode = _shm_inode(SHM_NAME)
      self._last_check = time.monotonic()
      self.last_seq = -1
      if os.name == "posix":
        try:
          from multiprocessing import resource_tracker
          resource_tracker.unregister(self.shm._name, "shared_memory")
        except Exception:
          pass
      return True
    except (FileNotFoundError, OSError):
      self.shm = None
      return False

  def _detach_shm(self):
    if self.shm is not None:
      try:
        self.shm.close()
      except Exception:
        pass
    self.shm = None
    self._inode = None

  def _segment_replaced(self):
    # If the segment was unlinked/re-created (e.g. by an older worker build),
    # we would keep reading a dead copy forever. Re-attach when the inode changes.
    now = time.monotonic()
    if now - self._last_check < SHM_CHECK_INTERVAL_S:
      return False
    self._last_check = now
    if self._inode is None:
      return False
    current = _shm_inode(SHM_NAME)
    return current != self._inode

  async def run_pipe_resiver(self):
    logger.info("Monitoring shared memory segment: %s", SHM_NAME)
    self.running = True

    while self.running:
      try:
        if not self._attach_shm():
          await asyncio.sleep(0.5)
          continue

        if self._segment_replaced():
          logger.info("Shared memory segment was replaced; re-attaching.")
          self._detach_shm()
          continue

        buf = self.shm.buf
        seq, length = struct.unpack_from("<II", buf, 0)

        if seq != self.last_seq and 0 < length <= (SHM_SIZE - 8):
          raw_bytes = bytes(buf[8 : 8 + length])
          self.last_seq = seq

          try:
            data = json.loads(raw_bytes.decode("utf-8"))
            if self.on_data_received:
              if asyncio.iscoroutinefunction(self.on_data_received):
                await self.on_data_received(data)
              else:
                self.on_data_received(data)
            else:
              logger.info("Received shared memory payload: %s", data)
          except Exception as e:
            logger.warning("Failed to decode shared memory payload: %s", e)

        await asyncio.sleep(0.05)

      except (FileNotFoundError, OSError):
        self._detach_shm()
        await asyncio.sleep(0.5)
      except Exception as e:
        if self.running:
          logger.warning("Shared memory receiver error: %s", e)
          await asyncio.sleep(0.5)

  def close(self):
    self.running = False
    self._detach_shm()
    logger.info("Shared memory receiver closed.")


class sender:

  def __init__(self):
    self.running = True
    self.shm = None
    self.seq = 0
    self._init_shm()

  def _init_shm(self):
    try:
      self.shm = shared_memory.SharedMemory(
          name=SHM_NAME, create=True, size=SHM_SIZE
      )
      struct.pack_into("<II", self.shm.buf, 0, 0, 0)
      logger.info("Created shared memory segment: %s", SHM_NAME)
    except FileExistsError:
      self.shm = shared_memory.SharedMemory(name=SHM_NAME)
      # Continue the sequence so the agent never mistakes a new event for an old one.
      try:
        self.seq, _ = struct.unpack_from("<II", self.shm.buf, 0)
      except Exception:
        self.seq = 0
      logger.info("Attached to existing shared memory segment: %s (seq=%s)", SHM_NAME, self.seq)
    except Exception as e:
      logger.exception("Failed to initialize shared memory sender: %s", e)
      self.shm = None
      return
    # The segment is shared with agent.py and must outlive this worker process:
    # stop Python's resource_tracker from unlinking it when the worker exits.
    if os.name == "posix":
      try:
        from multiprocessing import resource_tracker
        resource_tracker.unregister(self.shm._name, "shared_memory")
      except Exception:
        pass

  def connect(self):
    if self.shm is None:
      self._init_shm()
    return self.shm is not None

  def send_event(self, data):
    if self.shm is None:
      if not self.connect():
        return False

    try:
      payload_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
      length = len(payload_bytes)

      if length > (SHM_SIZE - 8):
        logger.error("Shared memory payload too large (%s bytes > %s).", length, SHM_SIZE - 8)
        return False
      self.seq = (self.seq + 1) % 0xFFFFFFFF

      buf = self.shm.buf
      buf[8 : 8 + length] = payload_bytes
      struct.pack_into("<II", buf, 0, self.seq, length)
      return True

    except Exception as e:
      logger.exception("Failed to write shared memory payload: %s", e)
      self.close()
      return False

  def close(self):
    # Do NOT unlink: agent.py stays attached to this segment across worker restarts.
    if self.shm:
      try:
        self.shm.close()
      except Exception:
        pass
      self.shm = None
    logger.info("Shared memory sender closed.")


if __name__ == "__main__":
  s = sender()
  try:
    while True:
      payload = {
          "status": {
              "camera": "ONLINE",
              "gpio": "ONLINE",
              "program_status": "RUNNING",
              "last_update": time.time(),
          }
      }
      s.send_event(payload)
      logger.info("Sent payload to shared memory.")
      time.sleep(2)
  except KeyboardInterrupt:
    s.close()

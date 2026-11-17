# platform_core/io_manager.py
from abc import ABC, abstractmethod
import logging
import threading
import time
from typing import Dict

logger = logging.getLogger("IOManager")


class BaseIOManager(ABC):
    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def read_input(self, channel: int) -> int:
        pass

    @abstractmethod
    def write_output(self, channel: int, value: bool) -> bool:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def close(self):
        pass


class AdvantechIOManager(BaseIOManager):
    """Thread-safe wrapper around the USB-4761.

    The main inspection loop and HealthMonitor thread both use the card. Every
    call is serialized by one RLock so the native BDaq driver is never disposed
    or re-created while another thread is reading/writing. The last requested
    value of every output is remembered and re-applied after a reconnect, so the
    PLC sees the same alarm/state/FB values once the card is back.
    """

    def __init__(self, device: str = "USB-4761,BID#0", auto_connect: bool = False):
        self.device = device
        self.gpio = None
        self._is_connected = False
        self._lock = threading.RLock()
        self._outputs: Dict[int, bool] = {}

        if auto_connect:
            self.connect()

    def connect(self, max_retries: int = 5, retry_delay: float = 2.0) -> bool:
        with self._lock:
            try:
                from .module.gpio.advantech import GPIO
                if self.gpio:
                    self.close()
                self._is_connected = False
                attempts = 0
                while not self._is_connected and attempts < max_retries:
                    attempts += 1
                    logger.info(f"[IOManager] Attempting to connect device {self.device} ({attempts}/{max_retries})...")
                    self.gpio = GPIO(device=self.device)
                    self._is_connected = bool(getattr(self.gpio, "is_connected", False))

                    if self._is_connected:
                        logger.info(f"[IOManager] Connected successfully to device {self.device}.")
                        self._restore_outputs()
                        return self._is_connected
                    else:
                        logger.warning(f"[IOManager] Connection attempt {attempts} failed. Retrying in {retry_delay}s...")
                        if attempts < max_retries:
                            time.sleep(retry_delay)

                logger.error(f"[IOManager] Failed to connect to {self.device} after {max_retries} attempts.")
                self._is_connected = False
                return False

            except Exception as e:
                logger.error(f"[IOManager] Connect failed: {e}")
                self._is_connected = False
                return False

    def _restore_outputs(self):
        # Caller holds self._lock.
        if not self._outputs:
            return
        for channel, value in sorted(self._outputs.items()):
            try:
                if not self.gpio.outputWrite(channel, on=value):
                    logger.error("[IOManager] Restore output ch%s=%s failed.", channel, value)
                    self._is_connected = False
                    return
            except Exception as e:
                logger.error("[IOManager] Restore output ch%s error: %s", channel, e)
                self._is_connected = False
                return
        logger.info("[IOManager] Restored outputs after reconnect: %s", dict(sorted(self._outputs.items())))

    def read_input(self, channel: int) -> int:
        with self._lock:
            if not self._is_connected or not self.gpio:
                return 0
            try:
                val = self.gpio.readInput(channel)
                if val is None:
                    self._is_connected = False
                    return 0
                return val
            except Exception as e:
                logger.error(f"[IOManager] read_input error: {e}")
                self._is_connected = False
                return 0

    def write_output(self, channel: int, value: bool) -> bool:
        with self._lock:
            # Remember the requested value even if the card is offline so it is
            # applied as soon as the card reconnects.
            self._outputs[int(channel)] = bool(value)
            if not self._is_connected or not self.gpio:
                return False
            try:
                success = bool(self.gpio.outputWrite(channel, on=value))
                if not success:
                    self._is_connected = False
                return success
            except Exception as e:
                logger.error(f"[IOManager] write_output error: {e}")
                self._is_connected = False
                return False

    def is_connected(self) -> bool:
        with self._lock:
            if not self._is_connected or not self.gpio:
                return False
            alive = bool(self.gpio.ping())
            self._is_connected = alive
            return alive

    def close(self):
        with self._lock:
            if self.gpio:
                try:
                    self.gpio.closeIO()
                except Exception:
                    pass
            self._is_connected = False
            self.gpio = None


class DummyIOManager(BaseIOManager):
    def connect(self) -> bool:
        return True

    def read_input(self, channel: int) -> int:
        return 0

    def write_output(self, channel: int, value: bool) -> bool:
        return True

    def is_connected(self) -> bool:
        return True

    def close(self):
        pass

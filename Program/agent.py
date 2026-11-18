import asyncio
import collections
import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import psutil

PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PROGRAM_DIR)
for _path in (ROOT_DIR, PROGRAM_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from Program.platform_core.module.getip import GetIPAddress
from Program.platform_core.module.MQTTControl import MQTT
from Program.platform_core.module.serverAPI import API_CALL
from platform_core.module.shm import resiver
from projects.SFS.config import Config
from projects.SFS.manifest import build_manifest

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AGENT")

# Worker auto-restart policy: restart a crashed worker, but give up (ERROR)
# after MAX_AUTO_RESTARTS restarts within RESTART_WINDOW_S.
WATCHDOG_INTERVAL_S = 2.0
MAX_AUTO_RESTARTS = 3
RESTART_WINDOW_S = 600.0


class Agent:

    def __init__(self):
        self.WORKER_DIR = PROGRAM_DIR
        self.WORKER_SCRIPT = "main.py"
        self.PYTHON = sys.executable
        self.LOG_DIR = os.path.join(ROOT_DIR, "logs")
        os.makedirs(self.LOG_DIR, exist_ok=True)
        self.WORKER_LOG = os.path.join(self.LOG_DIR, "worker_stdout.log")

        self.config = Config()
        self._refresh_network()
        self.api = API_CALL(config=self.config, manifest_builder=build_manifest)
        self._register_lock = threading.Lock()

        self.pipe = resiver(on_data_received=self.handle_incoming_pipe_data)

        self.payload = None
        self.last_status = None
        self.device_id = None
        self.MQTTServer = None
        self.api_key = None
        self.MQ = None

        self.worker_proc = None
        self._ctl_lock = threading.Lock()
        self._startup_lock = threading.Lock()
        # Watchdog state: _expect_running is True only while the worker should be up.
        self._expect_running = False
        self._crash_locked = False
        self._restart_times = collections.deque()
        self.last_log_pos = (
            os.path.getsize(self.WORKER_LOG) if os.path.exists(self.WORKER_LOG) else 0
        )

        self.reloadconfig(MQ=True)
        if self.MQ:
            self.MQ.sendHeardbeat(isRunning="ACTIVE")


        self.startup(register=True)

        threading.Thread(target=self._tail_log_worker, daemon=True).start()
        threading.Thread(target=self._republish_worker, daemon=True).start()
        threading.Thread(target=self._watchdog_worker, daemon=True).start()

    @property
    def stop_flag(self):
        return self.config.stop_flag_path

    def _refresh_network(self):
        try:
            net = GetIPAddress()
            # Register the IP of the port that reaches the web server: the web opens
            # the live stream back to this IP (edge:stream_port).
            route = None
            host = (self.config.server_url or "").strip()
            if host and host.lower() != "localhost":
                route = net.get_route_info(host, self.config.api_port)
            ip, mac, iface = route or net.get_active_lan_info()
            logger.info("Network: %s %s (%s)%s", ip, mac, iface,
                        f" -> route to {host}" if route else "")
            if ip != "127.0.0.1" and (ip != self.config.ip or mac != self.config.mac):
                self.config.update({"ip": ip, "mac": mac})
        except Exception as e:
            logger.warning("Network refresh failed: %s", e)

    def startup(self, register=False):
        with self._startup_lock:
            if register:
                threading.Thread(target=self._register_loop, daemon=True).start()

            pid = self.get_worker_pid()
            if pid:
                logger.info("Worker (main.py) is already running (PID: %s).", pid)
                self._expect_running = True
            elif self._crash_locked:
                logger.warning("Worker is in crash-lock (ERROR); not starting automatically.")
            else:
                logger.info("No worker running on startup. Starting worker now...")
                self.start_worker()

            is_running = self.get_worker_pid() is not None
            if is_running and self.last_status:
                self.payload = self.last_status
            else:
                self.payload = self._off_status(running=is_running)

            if self.MQ:
                self.MQ.statusMsg(msg=self.payload, api_key=self.api_key)
            logger.info("Start up complete.")

    def _register_loop(self):
        # One loop at a time (startup + re-register after settings change).
        if not self._register_lock.acquire(blocking=False):
            return
        try:
            self._register_until_ok()
        finally:
            self._register_lock.release()

    def _register_until_ok(self):
        while True:
            try:
                ok = asyncio.run(self.api.register())
            except Exception as e:
                # logger.warning("Register error: %s", e)
                ok = False
            if ok:
                logger.info("API server connected.")
                self.reloadconfig()
                return
            logger.warning("Server registration failed. Retrying in 10s...")
            time.sleep(10)

    def _off_status(self, running=False):
        status = self.MQ.off_payload() if self.MQ else {}
        if running:
            status["program_status"] = "RUNNING"
        elif self._crash_locked:
            status["program_status"] = "ERROR"
        else:
            status["program_status"] = "OFF"
        status["last_update"] = time.time()
        return status

    def _unwrap_status(self, data):
        status = data.get("status", data) if isinstance(data, dict) else None
        return dict(status) if isinstance(status, dict) else None

    def handle_incoming_pipe_data(self, data):
        self.payload = data
        status = self._unwrap_status(data)
        if status:
            self.last_status = status
            if self.MQ:
                self.MQ.statusMsg(msg=status, api_key=self.api_key)
        logger.info("Got real-time event from shared memory: %s", data)

    def _republish_worker(self):
        while True:
            time.sleep(5)
            try:
                if not self.MQ:
                    continue
                if self.get_worker_pid() is None:
                    self.last_status = None
                    status = self._off_status(running=False)
                elif self.last_status:
                    status = dict(self.last_status)
                    status["last_update"] = time.time()
                else:
                    status = self._off_status(running=True)
                self.MQ.statusMsg(msg=status, api_key=self.api_key)
            except Exception:
                logger.exception("Republish status failed")

    # ==========================
    # Worker watchdog
    # ==========================
    def _append_worker_log(self, message):
        # Also goes to the web terminal through _tail_log_worker.
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [AGENT]: {message}\n"
        try:
            with open(self.WORKER_LOG, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def _reset_crash_state(self):
        self._crash_locked = False
        self._restart_times.clear()

    def _watchdog_worker(self):
        while True:
            time.sleep(WATCHDOG_INTERVAL_S)
            try:
                self._watchdog_check()
            except Exception:
                logger.exception("Worker watchdog error")

    def _watchdog_check(self):
        if not self._expect_running or self._crash_locked:
            return
        if self._ctl_lock.locked():
            return  # start/stop in progress
        if self.get_worker_pid() is not None:
            return

        code = self.worker_proc.poll() if self.worker_proc is not None else None
        now = time.monotonic()
        while self._restart_times and now - self._restart_times[0] > RESTART_WINDOW_S:
            self._restart_times.popleft()

        if len(self._restart_times) >= MAX_AUTO_RESTARTS:
            self._expect_running = False
            self._crash_locked = True
            msg = (f"Worker crashed (exit code {code}) {MAX_AUTO_RESTARTS + 1} times within "
                   f"{int(RESTART_WINDOW_S // 60)} min. Auto-restart stopped -> ERROR.")
            logger.error(msg)
            self._append_worker_log(msg)
            for line in self._log_tail(15):
                logger.info("%s", line)
            if self.MQ:
                self.MQ.statusMsg(msg=self._off_status(running=False), api_key=self.api_key)
            return

        self._restart_times.append(now)
        msg = (f"Worker died unexpectedly (exit code {code}). Auto-restart "
               f"{len(self._restart_times)}/{MAX_AUTO_RESTARTS}...")
        logger.warning(msg)
        self._append_worker_log(msg)
        self.start_worker()

    def _tail_log_worker(self):
        while True:
            try:
                if self.MQ and self.device_id:
                    log_topic = f"{self.MQ.prefix}/{self.device_id}/logs"

                    if os.path.exists(self.WORKER_LOG):
                        curr_size = os.path.getsize(self.WORKER_LOG)

                        if curr_size > self.last_log_pos:
                            with open(
                                self.WORKER_LOG, "r", encoding="utf-8", errors="ignore"
                            ) as f:
                                f.seek(self.last_log_pos)
                                lines = f.readlines()
                                self.last_log_pos = f.tell()

                                for line in lines:
                                    clean_line = line.strip()
                                    if clean_line:
                                        mqtt_client = getattr(
                                            self.MQ, "client", getattr(self.MQ, "mqtt_client", None)
                                        )
                                        if mqtt_client:
                                            mqtt_client.publish(log_topic, clean_line)

                        elif curr_size < self.last_log_pos:
                            self.last_log_pos = 0

            except Exception:
                pass

            time.sleep(1.0)

    def reloadconfig(self, MQ=False):
        try:
            self.config.load()
            self.device_id = self.config.device_id
            self.MQTTServer = self.config.MQTTServer
            self.api_key = self.config.api_key

            if MQ:
                self.MQ = MQTT(
                    broker_url=self.MQTTServer,
                    device_id=self.device_id,
                    control=self.toggle_worker,
                    agentStartup=self.startup,
                    restart=self.restart_worker,
                    deletedevic=self.delete_device,
                    stream=self.stream_control,
                    camera_ids=[c["id"] for c in self.config.inspection_cameras],
                    api_key=self.api_key,
                    prefix=self.config.mqtt_prefix,
                )
                self._setup_mqtt()
                self.MQ.setMQTTServer(self.device_id, self.MQTTServer)

            logger.info("Reloaded config successfully.")
            return True
        except Exception as e:
            logger.exception("Failed to reload config: %s", e)
            return False

    def _setup_mqtt(self):
        self.MQ.callbackProgramcontrol = self.toggle_worker
        self.MQ.callbackRestart = self.restart_worker
        self.MQ.callbackDeletedevice = self.delete_device
        self.MQ.callbackStream = self.stream_control
        self.MQ.connectMqtt()

    def get_worker_pid(self):
        if self.worker_proc is not None and self.worker_proc.poll() is None:
            return self.worker_proc.pid
        worker_dir = os.path.realpath(self.WORKER_DIR)
        for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
            try:
                if proc.info["pid"] == os.getpid():
                    continue
                cmd = proc.info.get("cmdline") or []
                if len(cmd) < 2 or "python" not in os.path.basename(cmd[0]).lower():
                    continue
                if self.WORKER_SCRIPT not in [os.path.basename(c) for c in cmd[1:]]:
                    continue
                if os.path.realpath(proc.info.get("cwd") or "") == worker_dir:
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return None

    def start_worker(self):
        with self._ctl_lock:
            if self.get_worker_pid():
                self._expect_running = True
                return False
            reset_script = os.path.join(ROOT_DIR, "reconnectusb.sh")
            if os.path.exists(reset_script):
                logger.info("Executing camera reset script before starting worker...")
                try:
                    subprocess.run(["sudo", reset_script], check=True)
                    logger.info("Camera reset completed successfully.")
                except Exception as e:
                    logger.warning("Failed to run camera reset script: %s", e)
            time.sleep(30)
            try:
                os.remove(self.stop_flag)
            except OSError:
                pass

            log = open(self.WORKER_LOG, "ab")
            try:
                self.worker_proc = subprocess.Popen(
                    [self.PYTHON, "-u", self.WORKER_SCRIPT],
                    cwd=self.WORKER_DIR,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log.close()

            # From here on the watchdog restarts the worker if it dies.
            self._expect_running = True
            logger.info("Worker started (pid %s).", self.worker_proc.pid)
            for _ in range(30):
                time.sleep(0.1)
                code = self.worker_proc.poll()
                if code is not None:
                    logger.error("Worker exited immediately (code %s). Last log lines:", code)
                    for line in self._log_tail(15):
                        logger.info("%s", line)
                    return False
            return True

    def _log_tail(self, n=15):
        try:
            with open(self.WORKER_LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 8192))
                return f.read().decode("utf-8", errors="ignore").splitlines()[-n:]
        except OSError:
            return []

    def stop_worker(self, timeout=10):
        # Intentional stop: the watchdog must not restart the worker.
        self._expect_running = False
        with self._ctl_lock:
            self._expect_running = False
            pid = self.get_worker_pid()
            if not pid:
                return
            try:
                with open(self.stop_flag, "w", encoding="utf-8") as f:
                    f.write("STOP")
            except Exception as e:
                logger.warning("Cannot write stop flag %s (%s); will use signals.", self.stop_flag, e)
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return
            for sig, wait in (
                (None, timeout),
                (signal.SIGINT, 5),
                (signal.SIGTERM, 3),
                (signal.SIGKILL, 2),
            ):
                try:
                    if sig is not None:
                        proc.send_signal(sig)
                    proc.wait(timeout=wait)
                    return
                except psutil.TimeoutExpired:
                    continue
                except psutil.NoSuchProcess:
                    return

    def toggle_worker(self, data):
        logger.info("Toggle worker requested.")
        threading.Thread(target=self._toggle, daemon=True).start()

    def _toggle(self):
        try:
            pid = self.get_worker_pid()
            if pid:
                logger.info("Worker is running (pid %s); stopping.", pid)
                self.stop_worker()
            else:
                logger.info("Worker is not running; starting.")
                self._reset_crash_state()
                self.start_worker()
        except Exception as e:
            logger.exception("Failed to toggle worker: %s", e)

    def restart_worker(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        threading.Thread(target=self._restart, args=(data,), daemon=True).start()

    def _restart(self, data):
        self.config.update(data)
        logger.info("New config updated: %s", data)
        self.stop_worker()
        self._reset_crash_state()
        self.start_worker()
        logger.info("Worker process restarted.")
        # Device name / broker may have changed: follow it on MQTT, then send the
        # refreshed manifest (current setting values) to the web.
        self.device_id = self.config.device_id
        self.MQTTServer = self.config.MQTTServer
        self.api_key = self.config.api_key
        if self.MQ:
            self.MQ.camera_ids = [c["id"] for c in self.config.inspection_cameras]
            self.MQ.api_key = self.api_key
            self.MQ.setMQTTServer(self.device_id, self.MQTTServer)
        threading.Thread(target=self._register_loop, daemon=True).start()

    def stream_control(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = data.strip().lower() in ("1", "true", "on")
        if isinstance(data, bool):
            data = {"enabled": data}
        if not isinstance(data, dict):
            return
        if self.get_worker_pid() is None:
            logger.warning("Stream request ignored: worker is not running.")
            return
        payload = {
            "enabled": bool(data.get("enabled", False)),
            "camera_id": data.get("camera_id"),
        }
        path = self.config.stream_control_path
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except OSError as e:
            # Runs in the MQTT callback: never raise, but say clearly why live failed.
            logger.error("Stream control not applied: cannot write %s (%s). Check stop_flag_path.", path, e)
            return
        logger.info("Stream control: %s", payload)

    def delete_device(self, data):
        logger.info("Received delete command.")
        threading.Thread(target=self._delete, daemon=True).start()

    def _delete(self):
        self.stop_worker()
        self.config.update({
            "device_id": "",
            "ip": "",
            "mac": "",
            "MQTTServer": "",
            "api_key": "",
            "server_url": "",
        })
        self.cleanup()

    def cleanup(self):
        logger.info("Cleaning up resources...")
        self._expect_running = False
        try:
            self.pipe.close()
        except Exception:
            pass

        if self.MQ:
            self.MQ.statusMsg(self._off_status(), self.api_key)
            self.MQ.sendHeardbeat(isRunning="INACTIVE")
            time.sleep(0.5)
            self.MQ.disconnectMQTT()

        logger.info("Process terminating...")
        os._exit(0)


if __name__ == "__main__":

    lock_file = "/tmp/sfs_agent.lock"
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.warning("Another instance of agent.py is already running. Exiting.")
        sys.exit(0)

    if "--delay" in sys.argv:
        logger.info("Delay flag detected. Waiting 30s for system services...")
        time.sleep(30)

    a = Agent()
    try:
        asyncio.run(a.pipe.run_pipe_resiver())
    except KeyboardInterrupt:
        a.cleanup()
    except Exception as e:
        logger.exception("Fatal agent error: %s", e)
        a.cleanup()

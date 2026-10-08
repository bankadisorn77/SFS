import asyncio
import fcntl
import json
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

from ProcessClass.getip import GetIPAddress
from ProcessClass.MQTTControl import MQTT
from ProcessClass.serverAPI import API_CALL
from platform_core.module.shm import resiver
from projects.SFS.config import Config


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
        self.api = API_CALL(config=self.config)

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
        self.last_log_pos = (
            os.path.getsize(self.WORKER_LOG) if os.path.exists(self.WORKER_LOG) else 0
        )

        self.reloadconfig(MQ=True)
        if self.MQ:
            self.MQ.sendHeardbeat(isRunning="ACTIVE")

        # เรียก startup ครั้งแรก
        self.startup(register=True)

        threading.Thread(target=self._tail_log_worker, daemon=True).start()
        threading.Thread(target=self._republish_worker, daemon=True).start()

    @property
    def stop_flag(self):
        return self.config.stop_flag_path

    def _refresh_network(self):
        try:
            net = GetIPAddress()
            ip, mac, _ = net.get_active_lan_info()
            if ip != "127.0.0.1" and (ip != self.config.ip or mac != self.config.mac):
                self.config.update({"ip": ip, "mac": mac})
        except Exception as e:
            print(f"[AGENT] network refresh failed: {e}")

    def startup(self, register=False):
        with self._startup_lock:
            if register:
                threading.Thread(target=self._register_loop, daemon=True).start()

            pid = self.get_worker_pid()
            if pid:
                print(f"[AGENT] Worker (main.py) is already running (PID: {pid}).")
            else:
                print("[AGENT] No worker running on startup. Starting worker now...")
                self.start_worker()

            is_running = self.get_worker_pid() is not None
            if is_running and self.last_status:
                self.payload = self.last_status
            else:
                self.payload = self._off_status(running=is_running)

            if self.MQ:
                self.MQ.statusMsg(msg=self.payload, api_key=self.api_key)
            print("[AGENT] START UP COMPLETE")

    def _register_loop(self):
        while True:
            try:
                ok = asyncio.run(self.api.register())
            except Exception as e:
                print(f"[AGENT] Register error: {e}")
                ok = False
            if ok:
                print("[API Server Connected]")
                self.reloadconfig()
                return
            print("[AGENT] Server registration failed. Retrying in 10s...")
            time.sleep(10)

    def _off_status(self, running=False):
        status = self.MQ.off_payload() if self.MQ else {}
        status["program_status"] = "RUNNING" if running else "OFF"
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
        print(f"[MAIN CALLBACK] Got real-time event from RAM: {data}")

    def _republish_worker(self):
        while True:
            time.sleep(10)
            try:
                if not self.MQ:
                    continue
                if self.get_worker_pid() is None:
                    self.last_status = None
                    status = self._off_status()
                elif self.last_status:
                    status = dict(self.last_status)
                    status["last_update"] = time.time()
                else:
                    continue
                self.MQ.statusMsg(msg=status, api_key=self.api_key)
            except Exception:
                pass

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
                )
                self._setup_mqtt()
                self.MQ.setMQTTServer(self.device_id, self.MQTTServer)

            print("[AGENT] Reload config successfully")
            return True
        except Exception as e:
            print(f"[ERROR] in reloadconfig: {e}")
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
                return False
            reset_script = os.path.join(ROOT_DIR, "reconnectusb.sh")
            if os.path.exists(reset_script):
                print("[AGENT] Executing camera reset script before starting worker...")
                try:
                    subprocess.run(["sudo", reset_script], check=True)
                    print("[AGENT] Camera reset completed successfully.")
                except Exception as e:
                    print(f"[AGENT WARNING] Failed to run camera reset script: {e}")
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

            print(f"[AGENT] Worker started (pid {self.worker_proc.pid}).")
            for _ in range(30):
                time.sleep(0.1)
                code = self.worker_proc.poll()
                if code is not None:
                    print(f"[AGENT] Worker exited immediately (code {code}). Last log lines:")
                    for line in self._log_tail(15):
                        print(f"  {line}")
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
        with self._ctl_lock:
            pid = self.get_worker_pid()
            if not pid:
                return
            try:
                with open(self.stop_flag, "w", encoding="utf-8") as f:
                    f.write("STOP")
            except Exception:
                pass
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
        print("[AGENT] toggle_worker")
        threading.Thread(target=self._toggle, daemon=True).start()

    def _toggle(self):
        try:
            pid = self.get_worker_pid()
            if pid:
                print(f"[AGENT] Worker is running (pid {pid}) -> stopping.")
                self.stop_worker()
            else:
                print("[AGENT] Worker is not running -> starting.")
                self.start_worker()
        except Exception as e:
            print(f"[AGENT] toggle failed: {e}")

    def restart_worker(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        threading.Thread(target=self._restart, args=(data,), daemon=True).start()

    def _restart(self, data):
        self.config.update(data)
        print(f"[AGENT] New Config Updated: {data}")
        self.stop_worker()
        self.start_worker()
        print("[AGENT] Worker process restarted.")

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
            print("[AGENT] Stream request ignored: worker is not running")
            return
        payload = {
            "enabled": bool(data.get("enabled", False)),
            "camera_id": data.get("camera_id"),
        }
        path = self.config.stream_control_path
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
        print(f"[AGENT] Stream control: {payload}")

    def delete_device(self, data):
        print("[AGENT] Received delete command")
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
        print("[AGENT] Cleaning up resources...")
        try:
            self.pipe.close()
        except Exception:
            pass

        if self.MQ:
            self.MQ.statusMsg(self._off_status(), self.api_key)
            self.MQ.sendHeardbeat(isRunning="INACTIVE")
            time.sleep(0.5)
            self.MQ.disconnectMQTT()

        print("[AGENT] Process terminating...")
        os._exit(0)


if __name__ == "__main__":
    # ตรวจสอบ Single Instance ทันที
    lock_file = "/tmp/sfs_agent.lock"
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[AGENT] Another instance of agent.py is already running. Exiting.")
        sys.exit(0)

    # หน่วงเวลารอเซอร์วิสเฉพาะเมื่อส่งแฟล็ก --delay (สำหรับตอนเปิดเครื่อง)
    if "--delay" in sys.argv:
        print("[AGENT] Delay flag detected. Waiting 30s for system services...")
        time.sleep(30)

    a = Agent()
    try:
        asyncio.run(a.pipe.run_pipe_resiver())
    except KeyboardInterrupt:
        a.cleanup()
    except Exception as e:
        print(f"[AGENT FATAL ERROR]: {e}")
        a.cleanup()
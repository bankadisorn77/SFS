import datetime
import json
import logging
import paho.mqtt.client as mqtt
from Program.platform_core.module.getip import GetIPAddress
from Program.platform_core.module.repeatedTimer import RepeatedTimer
import threading  

logger = logging.getLogger(__name__)


class MQTT:

  def __init__(
      self,
      broker_url="127.0.0.1",
      device_id="BG-01",
      api_key=None,
      control=None,
      agentStartup=None,
      restart=None,
      deletedevic=None,
      stream=None,
      camera_ids=None,
  ):
    self.prefix = "SFS_YOKA_POKE"
    self.camera_ids = list(camera_ids or [])
    self.MQTTserver = broker_url
    self.machineID = device_id
    self.api_key = api_key
    self.MQTTConnected = False
    self.agentStartup = agentStartup
    self._has_started = False  

    
    try:
      self.mqtt_client = mqtt.Client(
        client_id=self.machineID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
      )
    except AttributeError:
      self.mqtt_client = mqtt.Client()

    self.mqtt_client.on_connect = self.onConnectedMqtt
    self.mqtt_client.on_disconnect = self.onDisconnectedMqtt
    self.mqtt_client.on_message = self.onMessageMqtt

    # ตั้งค่า Auto-reconnect delay
    self.mqtt_client.reconnect_delay_set(min_delay=2, max_delay=30)

    # Callbacks
    self.callbackProgramcontrol = control
    self.callbackRestart = restart
    self.callbackDeletedevice = deletedevic
    self.callbackStream = stream

    self.currentMachineIP, _, _ = GetIPAddress().get_active_lan_info()
    self.timer = RepeatedTimer(30, self.sendHeardbeat)
    self.useRobot = False


  def topic(self):
    prefix = self.prefix
    pub_status = f"{prefix}/{self.machineID}/status"
    pub_heartbeat = f"{prefix}/{self.machineID}/heartbeat"

    sub_programcontrol = f"{prefix}/{self.machineID}/program/control"
    sub_programconfig = f"{prefix}/{self.machineID}/program/config"
    sub_deletedevice = f"{prefix}/{self.machineID}/delete"
    sub_streamcontrol = f"{prefix}/{self.machineID}/program/stream"

    return [pub_status, pub_heartbeat], [
        sub_programcontrol,
        sub_programconfig,
        sub_deletedevice,
        sub_streamcontrol,
    ]

  def setMQTTServer(self, machineID, MQTTserver):
    if (
        (MQTTserver != self.MQTTserver)
        or (machineID != self.machineID)
        or (not self.MQTTConnected)
    ):
      self.disconnectMQTT()
      self.MQTTserver = MQTTserver
      self.machineID = machineID
      self.currentMachineIP, _, _ = GetIPAddress().get_active_lan_info()
      logger.info("New target server: %s | Device ID: %s", self.MQTTserver, self.machineID)
      self.connectMqtt()

  def connectMqtt(self):
    try:
      lwt_topic1 = f'{self.prefix}/{self.machineID}/status'
      lwt_topic2 = f'{self.prefix}/{self.machineID}/heartbeat'
      lwt_dict1 = self.off_payload()
      lwt_dict1['api_key'] = self.api_key
      lwt_dict1['last_update'] = datetime.datetime.now().timestamp()
      lwt_payload1 = json.dumps(lwt_dict1)

      lwt_dict2 =  {
            "IP": GetIPAddress().get_active_lan_info()[0],
            "name": self.machineID,
            "Running Status": 'INACTIVE',
            "last_update": datetime.datetime.now().strftime("%H:%M:%S"),
        }
      lwt_payload2 = json.dumps(lwt_dict2)

      self.mqtt_client.will_set(
          topic=lwt_topic1, payload=lwt_payload1, qos=1, retain=False
      )
      self.mqtt_client.will_set(
          topic=lwt_topic2, payload=lwt_payload2, qos=1, retain=False
      )
      port = 1883
      self.mqtt_client.connect_async(self.MQTTserver, port, keepalive=15)
      self.mqtt_client.loop_start()

      if not self.timer.is_running:
        self.timer.start()
    except Exception as e:
      logger.exception("MQTT connection setup error: %s", e)
      self.MQTTConnected = False

  def disconnectMQTT(self):
    try:
      self.MQTTConnected = False
      if self.timer.is_running:
        self.timer.stop()
      self.mqtt_client.loop_stop()
      self.mqtt_client.disconnect()
      logger.info("Disconnected cleanly.")
    except Exception:
      pass

  # ==================== Logging & Callbacks ====================
  def onProgramcontrol(self, data):
    if self.callbackProgramcontrol is not None:
      self.callbackProgramcontrol(data)

  def onProgramconfig(self, data):
    if self.callbackRestart is not None:
      self.callbackRestart(data)

  def onDeletedevice(self, data):
    logger.info("Received delete command.")
    if self.callbackDeletedevice is not None:
      self.callbackDeletedevice(data)

  def onStreamcontrol(self, data):
    if self.callbackStream is not None:
      self.callbackStream(data)

  # ==================== Publish Data ====================

  def off_payload(self):
    return {
        "program_status": "OFF",
        "camera": {cam_id: False for cam_id in self.camera_ids},
        "gpio": False,
        "round_1_input": False,
        "round_2_input": False,
        "machine_status": False,
        "alarm_status": False,
        "round_1_output": False,
        "round_2_output": False,
        "stream_status": False,
        "stream_camera": "",
    }

  def statusMsg(self, msg, api_key):
    if isinstance(msg, str):
      try:
        msg = json.loads(msg)
      except Exception:
        msg = {}

    if not isinstance(msg, dict):
      return

    payload = self.off_payload()
    payload.update(msg)
    payload["api_key"] = api_key
    payload.setdefault("last_update", datetime.datetime.now().timestamp())
    self.publish(json.dumps(payload, default=str), 0)

  def sendHeardbeat(self, isRunning="ACTIVE"):
    if self.MQTTConnected:
      try:
        param = {
            "IP": GetIPAddress().get_active_lan_info()[0],
            "name": self.machineID,
            "Running Status": isRunning,
            "last_update": datetime.datetime.now().strftime("%H:%M:%S"),
        }
        self.publish(json.dumps(param), 1)
      except Exception:
        pass

  def publish(self, msg, i):
    if self.MQTTConnected:
      try:
        topic, _ = self.topic()
        self.mqtt_client.publish(topic[i], msg)
      except Exception as e:
        logger.warning("MQTT publish error: %s", e)

  # ==================== MQTT Events ====================

  def onConnectedMqtt(self, client, userdata, flags, rc, properties=None):
    if rc == 0:
      self.MQTTConnected = True
      _, sub_topics = self.topic()
      for t in sub_topics:
        self.mqtt_client.subscribe(t, qos=0)

      logger.info("Connected to MQTT (rc=0).")
      if not self._has_started and callable(self.agentStartup):
        self._has_started = True
        threading.Thread(target=self.agentStartup, daemon=True).start()
    else:
      self.MQTTConnected = False

  def onDisconnectedMqtt(self, client, userdata, *args):
    self.MQTTConnected = False
    logger.warning("Disconnected from MQTT; client will auto-reconnect in background.")

  def onMessageMqtt(self, client, userdata, msg):
    _, sub_topics = self.topic()
    try:
      payload_str = msg.payload.decode("utf-8").strip()

      try:
        data = json.loads(payload_str)
      except json.JSONDecodeError:
        data = payload_str

      if msg.topic == sub_topics[0]:
        logger.info("Received program control: %s", data)
        self.onProgramcontrol(data)
      elif msg.topic == sub_topics[1]:
        if isinstance(data, str):
          try:
            data = json.loads(data)
          except Exception:
            data = {}
        self.onProgramconfig(data)
      elif msg.topic == sub_topics[2]:
        if isinstance(data, str):
          try:
            data = json.loads(data)
          except Exception:
            pass
        self.onDeletedevice(data)
      elif msg.topic == sub_topics[3]:
        if isinstance(data, str):
          try:
            data = json.loads(data)
          except Exception:
            pass
        self.onStreamcontrol(data)

    except Exception as e:
      logger.exception("Error handling MQTT message: %s", e)
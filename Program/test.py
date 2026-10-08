import json

import paho.mqtt.publish as publish

class Controller:
    def __init__(self, machine_id: str):
        self.machineID = machine_id

    def trigger_control(self, prefix: str, broker_ip: str = "127.0.0.1"):
        
        topic = f"{prefix}/{self.machineID}/program/control"
        payload = {
            "enable": False,
            "camera_id": "cam_1"
        }
        
        publish.single(
            topic=topic,
            payload=json.dumps(payload),
            hostname=broker_ip,
            port=1883
        )
        print(f"Triggered -> {topic}")

if __name__ == '__main__':
    app = Controller(machine_id="UTL-Ai-SFS(Dev)")
    app.trigger_control(prefix="SFS_YOKA_POKE", broker_ip="169.254.26.65")
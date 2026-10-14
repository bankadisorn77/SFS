import logging

import httpx

logger = logging.getLogger(__name__)


class API_CALL():
    def __init__(self,config=None):
        self.config = config
        self.api_key =  self.config.api_key

    @property
    def url(self):
        return f'http://{self.config.server_url}:{getattr(self.config, "api_port", 8080)}'

    @staticmethod
    def _channel(items, name):
        for item in items or []:
            if isinstance(item, dict) and item.get("name") == name:
                return item.get("channel")
        return None

    @staticmethod
    def _channel_map(items):
        return {
            item["name"]: item.get("channel")
            for item in items or []
            if isinstance(item, dict) and "name" in item
        }

    async def register(self):
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f'{self.url}/register_device'
            cfg = self.config
            payload = {
                'name': cfg.device_id,
                'ip_address': cfg.ip,
                'mac_address': cfg.mac,
                'io_channel':{
                    'input_channel': self._channel(cfg.inputChannel, 'round_1'),
                    'output_alarm_channel': self._channel(cfg.outputChannel, 'alarm'),
                    'output_relay_channel': self._channel(cfg.outputChannel, 'contor'),
                    'output_light_channel': self._channel(cfg.outputChannel, 'state_machine'),
                },
                'model_path': getattr(cfg, 'model_path', ''),
                'save_image_path': cfg.save_image_path,
                'mqtt_broker': cfg.MQTTServer,
                'project_id': cfg.project_id,
                'camera_config': cfg.cameras,
                'project_config': {'project_id': cfg.project_id, 'pipelines': cfg.pipelines},
                'io_config': {
                    'input': self._channel_map(cfg.inputChannel),
                    'output': self._channel_map(cfg.outputChannel),
                },
            }
            try:
                res = await client.post(url=url,json=payload)
                if res.status_code ==200:
                    data = res.json()
                    self.config.update(data)
                    return True
                else:
                    return False
            except httpx.CloseError:
                logger.error("Cannot connect to server.")
                return False
            except httpx.TimeoutException:
                 logger.error("Server request timed out.")
                 return False
            except Exception as e:
                logger.exception("Device registration failed: %s", e)
                return False

    async def newAlarm(self,imagebase64 :str = None , detection_info :str = None, cycle_id=None, camera_id=None, pipeline_id=None):
        self.api_key =  self.config.api_key
        async with httpx.AsyncClient(timeout=10) as client:
            url = f'{self.url}/image_log'
            header ={"X-API-Key":self.api_key}
            payload = {
                'image':imagebase64,
                'detected_objects':str(detection_info),
                'cycle_id': cycle_id,
                'camera_id': camera_id,
                'pipeline_id': pipeline_id,
            }
            try:
                res = await client.post(url=url,headers=header,json=payload)
                if res.status_code == 200:
                    logger.info("Image log sent successfully.")
                else:
                    logger.warning(
                        "Image log request failed (%s): %s",
                        res.status_code,
                        res.json(),
                    )
            except httpx.CloseError:
                logger.error("Cannot connect to server.")

            except httpx.TimeoutException:
                logger.error("Server request timed out.")
            except Exception as e:
                logger.exception("Image log request failed: %s", e)
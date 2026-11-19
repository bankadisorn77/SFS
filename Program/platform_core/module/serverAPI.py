import logging

import httpx

logger = logging.getLogger(__name__)


class API_CALL():
    def __init__(self, config=None, manifest_builder=None):
        self.config = config
        self.api_key = self.config.api_key
        # manifest_builder(config) -> dict describing this device for the web.
        self.manifest_builder = manifest_builder

    @property
    def url(self):
        return f'http://{self.config.server_url}:{getattr(self.config, "api_port", 8080)}'

    @staticmethod
    def _channel(items, name):
        if not isinstance(items, (list, tuple)):
            return None
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                return item.get("channel")
        return None

    @staticmethod
    def _channel_map(items):
        if not isinstance(items, (list, tuple)):
            return {}
        return {
            item["name"]: item.get("channel")
            for item in items
            if isinstance(item, dict) and "name" in item
        }

    def _legacy_io(self):
        """Legacy io fields for older web servers. A project config may provide
        legacy_io() itself (BG uses single channel numbers, not name lists)."""
        cfg = self.config
        custom = getattr(cfg, "legacy_io", None)
        if callable(custom):
            try:
                io_channel, io_config = custom()
                return io_channel, io_config
            except Exception:
                logger.exception("legacy_io() failed")
        inputs = getattr(cfg, "inputChannel", None)
        outputs = getattr(cfg, "outputChannel", None)
        io_channel = {
            'input_channel': self._channel(inputs, 'round_1'),
            'output_alarm_channel': self._channel(outputs, 'alarm'),
            'output_light_channel': self._channel(outputs, 'state_machine'),
        }
        io_config = {'input': self._channel_map(inputs), 'output': self._channel_map(outputs)}
        return io_channel, io_config

    def _manifest(self):
        if not callable(self.manifest_builder):
            return None
        try:
            return self.manifest_builder(self.config)
        except Exception:
            logger.exception("Cannot build device manifest")
            return None

    async def register(self):
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f'{self.url}/register_device'
            cfg = self.config
            io_channel, io_config = self._legacy_io()
            payload = {
                'name': cfg.device_id,
                'ip_address': cfg.ip,
                'mac_address': cfg.mac,
                'api_key': cfg.api_key if cfg.api_key not in (None, '', 'api_key') else None,
                'project_id': cfg.project_id,
                'manifest': self._manifest(),
                # Legacy fields kept for older web servers.
                'io_channel': io_channel,
                'save_image_path': getattr(cfg, 'save_image_path', ''),
                'mqtt_broker': cfg.MQTTServer,
                'camera_config': getattr(cfg, 'cameras', []),
                'io_config': io_config,
            }
            try:
                res = await client.post(url=url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, dict) and data.get('api_key'):
                        self.config.update({'api_key': data['api_key']})
                        self.api_key = data['api_key']
                    return True
                logger.error('Device registration rejected (%s): %s', res.status_code, res.text[:200])
                return False
            except httpx.TimeoutException:
                logger.error("Server request timed out.")
                return False
            except Exception:
                logger.error("Device registration failed.")
                return False

    async def newAlarm(self, imagebase64: str = None, detection_info: str = None, cycle_id=None, camera_id=None, pipeline_id=None):
        self.api_key = self.config.api_key
        async with httpx.AsyncClient(timeout=10) as client:
            url = f'{self.url}/image_log'
            header = {"X-API-Key": self.api_key}
            payload = {
                'image': imagebase64,
                'detected_objects': str(detection_info),
                'cycle_id': cycle_id,
                'camera_id': camera_id,
                'pipeline_id': pipeline_id,
            }
            try:
                res = await client.post(url=url, headers=header, json=payload)
                if res.status_code == 200:
                    logger.info("Image log sent successfully.")
                else:
                    logger.warning("Image log request failed (%s): %s", res.status_code, res.text[:200])
            except httpx.TimeoutException:
                logger.error("Server request timed out.")
            except Exception as e:
                logger.exception("Image log request failed: %s", e)

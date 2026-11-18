import os
import socket
import fcntl
import logging
import struct
import time

logger = logging.getLogger(__name__)


class GetIPAddress:
    def __init__(self):
        pass

    def is_physical_ethernet(self, ifname: str) -> bool:
        """ตรวจสอบว่าเป็นพอร์ต LAN กายภาพจริง (ไม่ใช่ Wi-Fi, Virtual, หรือ Loopback)"""
        net_path = f"/sys/class/net/{ifname}"
        
        # 1. ข้าม loopback
        if ifname == "lo":
            return False

        # 2. ข้ามอินเทอร์เฟซเสมือน (Docker, Podman, VM)
        virtual_prefixes = ('docker', 'podman', 'veth', 'br-', 'virbr', 'tun', 'tap')
        if ifname.startswith(virtual_prefixes):
            return False

        # 3. ข้าม Wi-Fi (ถ้าเป็นการ์ด Wi-Fi จะมีโฟลเดอร์ชื่อ wireless อยู่ข้างใน)
        if os.path.exists(f"{net_path}/wireless") or os.path.exists(f"{net_path}/phy80211"):
            return False

        # 4. ตรวจสอบว่าสายเชื่อมต่ออยู่จริงหรือไม่ (1 = เสียบสาย/ลิงก์ทำงาน)
        try:
            with open(f"{net_path}/carrier", "r") as f:
                if f.read().strip() != "1":
                    return False
        except Exception:
            return False

        return True

    def get_ip_address(self, interface: str):
        """ดึง IPv4 ของ Interface นั้นๆ"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip = fcntl.ioctl(
                sock.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack('256s', interface[:15].encode('utf-8'))
            )[20:24]
            sock.close()
            return socket.inet_ntoa(ip)
        except Exception:
            return None

    def get_mac_address(self, interface: str):
        """ดึง MAC Address ตรงจากโฟลเดอร์ Interface"""
        try:
            with open(f"/sys/class/net/{interface}/address", "r") as f:
                return f.read().strip().upper()
        except Exception:
            return "00:00:00:00:00:00"

    def get_active_lan_info(self):
        """วนหาพอร์ตแลนที่ผ่านเงื่อนไขและมี IP พร้อมใช้งาน"""
        interfaces = [name for _, name in socket.if_nameindex()]
        
        for iface in interfaces:
            if self.is_physical_ethernet(iface):
                ip = self.get_ip_address(iface)
                # ต้องมี IP และไม่ใช่ Loopback (127.x.x.x)
                if ip and not ip.startswith("127."):
                    mac = self.get_mac_address(iface)
                    return ip, mac, iface
                    
        return "127.0.0.1", "00:00:00:00:00:00", None

    def get_route_info(self, host: str, port: int = 80):
        """IP/MAC/interface this machine uses to reach `host` (e.g. the web server).

        With two LAN ports (factory network + direct cable to the web PC) the first
        port found by get_active_lan_info() may not be reachable from the web, and
        the live view (web -> edge:8081) would fail. A UDP connect() only asks the
        kernel for the route; no packet is sent. Returns None if not resolvable.
        """
        host = (host or "").strip()
        if not host:
            return None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((host, int(port or 80)))
                ip = sock.getsockname()[0]
        except (OSError, ValueError):
            return None
        if not ip or ip.startswith("127.") or ip == "0.0.0.0":
            return None
        try:
            names = [name for _, name in socket.if_nameindex()]
        except OSError:
            names = []
        for iface in names:
            if self.get_ip_address(iface) == ip:
                return ip, self.get_mac_address(iface), iface
        return ip, "00:00:00:00:00:00", None

    def loopCheck(self, retry_delay: float = 3.0):
        """วนลูปรอจนกว่าจะเสียบสายแลนและได้ IP เรียบร้อย"""
        ip, mac, iface = self.get_active_lan_info()
        while ip.startswith("127.") or iface is None:
            logger.warning("Wired network is not ready; waiting for cable/IP...")
            time.sleep(retry_delay)
            ip, mac, iface = self.get_active_lan_info()
            
        return ip, mac, iface


if __name__ == '__main__':
    detector = GetIPAddress()
    ip, mac, iface = detector.loopCheck()
    print("Connected on [%s] -> IP: %s | MAC: %s", iface, ip, mac)

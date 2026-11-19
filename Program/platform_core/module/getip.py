"""Find the IP / MAC of the wired LAN port (Linux and Windows).

The web server opens the live stream back to the IP the edge registers, and the
heartbeat shows it, so it must be the IP of the *wired* LAN port, never Wi-Fi,
Bluetooth, VPN or a virtual adapter (Docker, Hyper-V vEthernet, VMware, ...).

Linux  : /sys/class/net tells physical / wireless / cable state.
Windows: psutil adapter names + link state (Windows has no /sys and no fcntl).
"""
import logging
import os
import re
import socket
import sys
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is an agent dependency
    psutil = None

IS_WINDOWS = sys.platform.startswith("win") or os.name == "nt"
NO_IP = ("127.0.0.1", "00:00:00:00:00:00", None)

# Adapter names that are never the wired LAN port (matched case-insensitively).
_EXCLUDED_NAME = re.compile(
    r"(wi-?fi|wlan|wireless|802\.11|bluetooth|vethernet|hyper-?v|virtual|vmware|virtualbox|vbox|"
    r"loopback|pseudo|teredo|isatap|6to4|tailscale|zerotier|wireguard|openvpn|vpn|wsl|docker|"
    r"podman|npcap|tap|tun|^lo$|^veth|^br-|^virbr)",
    re.IGNORECASE,
)
_LINUX_VIRTUAL_PREFIXES = ("docker", "podman", "veth", "br-", "virbr", "tun", "tap", "wg", "zt", "tailscale")

InfoTuple = Tuple[str, str, Optional[str]]


def _norm_mac(mac: Optional[str]) -> str:
    if not mac:
        return "00:00:00:00:00:00"
    return mac.replace("-", ":").upper()


class GetIPAddress:
    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # adapter classification
    # ------------------------------------------------------------------
    def _linux_is_physical_ethernet(self, ifname: str) -> bool:
        net_path = f"/sys/class/net/{ifname}"
        if ifname == "lo" or ifname.startswith(_LINUX_VIRTUAL_PREFIXES):
            return False
        # Wi-Fi cards have a "wireless" / "phy80211" entry.
        if os.path.exists(f"{net_path}/wireless") or os.path.exists(f"{net_path}/phy80211"):
            return False
        try:
            with open(f"{net_path}/carrier", "r") as f:
                return f.read().strip() == "1"  # 1 = cable plugged / link up
        except OSError:
            return False

    def _windows_is_physical_ethernet(self, ifname: str) -> bool:
        if _EXCLUDED_NAME.search(ifname or ""):
            return False
        stats = self._stats().get(ifname)
        return bool(stats is None or getattr(stats, "isup", False))

    def is_physical_ethernet(self, ifname: str) -> bool:
        """True for a wired LAN port with link up (not Wi-Fi, virtual or loopback)."""
        if not ifname:
            return False
        if IS_WINDOWS:
            return self._windows_is_physical_ethernet(ifname)
        return self._linux_is_physical_ethernet(ifname)

    # ------------------------------------------------------------------
    # addresses (psutil works on both OS)
    # ------------------------------------------------------------------
    @staticmethod
    def _addrs():
        if psutil is None:
            return {}
        try:
            return psutil.net_if_addrs()
        except Exception:
            return {}

    @staticmethod
    def _stats():
        if psutil is None:
            return {}
        try:
            return psutil.net_if_stats()
        except Exception:
            return {}

    def get_ip_address(self, interface: str) -> Optional[str]:
        for addr in self._addrs().get(interface, []):
            if addr.family == socket.AF_INET and addr.address:
                return addr.address
        return None

    def get_mac_address(self, interface: str) -> str:
        link = getattr(psutil, "AF_LINK", None) if psutil else None
        for addr in self._addrs().get(interface, []):
            if link is not None and addr.family == link and addr.address:
                return _norm_mac(addr.address)
        if not IS_WINDOWS:
            try:
                with open(f"/sys/class/net/{interface}/address", "r") as f:
                    return _norm_mac(f.read().strip())
            except OSError:
                pass
        return "00:00:00:00:00:00"

    def _interface_of_ip(self, ip: str) -> Optional[str]:
        for name in self._addrs():
            if self.get_ip_address(name) == ip:
                return name
        return None

    def wired_interfaces(self) -> List[InfoTuple]:
        """All wired LAN ports with an IPv4 address; routable addresses first,
        169.254.x.x (direct cable without DHCP) after them."""
        found = []
        for name in self._addrs():
            if not self.is_physical_ethernet(name):
                continue
            ip = self.get_ip_address(name)
            if not ip or ip.startswith("127.") or ip == "0.0.0.0":
                continue
            found.append((ip, self.get_mac_address(name), name))
        found.sort(key=lambda item: item[0].startswith("169.254."))
        return found

    def get_active_lan_info(self) -> InfoTuple:
        """(ip, mac, interface) of the first wired LAN port, or NO_IP."""
        ports = self.wired_interfaces()
        return ports[0] if ports else NO_IP

    def get_route_info(self, host: str, port: int = 80) -> Optional[InfoTuple]:
        """IP/MAC/interface this machine uses to reach `host` (e.g. the web server),
        only if that route goes through a wired LAN port.

        With two LAN ports (factory network + direct cable to the web PC) the first
        port found may not be reachable from the web, and the live view
        (web -> edge:stream_port) would fail. A UDP connect() only asks the OS for
        the route; no packet is sent. Returns None if not resolvable or if the route
        uses Wi-Fi / a virtual adapter (then the caller falls back to the LAN port).
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
        iface = self._interface_of_ip(ip)
        if iface is None:
            return None
        if not self.is_physical_ethernet(iface):
            logger.warning("Route to %s uses %s (%s), not a wired LAN port; using the LAN port instead.",
                           host, iface, ip)
            return None
        return ip, self.get_mac_address(iface), iface

    # ------------------------------------------------------------------
    # legacy helpers
    # ------------------------------------------------------------------
    def get_local_ip(self) -> str:
        return self.get_active_lan_info()[0]

    def get_local_mac(self) -> str:
        return self.get_active_lan_info()[1]

    def loopCheck(self, retry_delay: float = 3.0, max_wait_s: Optional[float] = None) -> InfoTuple:
        """Wait until a wired LAN port has an IP. max_wait_s=None waits forever;
        otherwise returns NO_IP after max_wait_s (inspection must not depend on LAN)."""
        start = time.monotonic()
        info = self.get_active_lan_info()
        while info[2] is None:
            if max_wait_s is not None and time.monotonic() - start >= max_wait_s:
                return info
            logger.warning("Wired network is not ready; waiting for cable/IP...")
            time.sleep(retry_delay)
            info = self.get_active_lan_info()
        return info


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    detector = GetIPAddress()
    for item in detector.wired_interfaces():
        print("Wired LAN [%s] -> IP: %s | MAC: %s" % (item[2], item[0], item[1]))
    print("Selected:", detector.get_active_lan_info())

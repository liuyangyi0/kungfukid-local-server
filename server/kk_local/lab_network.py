"""Explicit host-only two-VM fixture, not network account authentication.

Production/local password endpoints remain loopback-only. Each experimental
endpoint admits one preconfigured VM address and one account via the offline
adapter. IP admission is not a credential and this must never be exposed to LAN.
"""
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network


@dataclass(frozen=True)
class LabEndpoint:
    host: str
    peer: str
    network: str

    def __post_init__(self):
        try:
            subnet = IPv4Network(self.network, strict=True)
            host, peer = IPv4Address(self.host), IPv4Address(self.peer)
        except (ValueError, TypeError) as exc:
            raise ValueError('literal IPv4 lab endpoint required') from exc
        private = (IPv4Network('10.0.0.0/8'), IPv4Network('172.16.0.0/12'),
                   IPv4Network('192.168.0.0/16'))
        if (not 24 <= subnet.prefixlen <= 29 or not any(subnet.subnet_of(n) for n in private)
                or any(ip not in subnet or ip in (subnet.network_address, subnet.broadcast_address)
                                      for ip in (host, peer))):
            raise ValueError('lab endpoints require usable hosts in one small RFC1918 subnet')

    def accepts(self, address):
        return bool(address) and address[0] == self.peer

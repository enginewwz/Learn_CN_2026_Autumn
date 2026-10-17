#!/usr/bin/python3
# exp2 topology and test runner.
#
#   h1 --- r1 --- r2 --- r3 --- h2      (3 hops between the two terminals)
#
#   h1-eth0  10.0.1.11/24   default via 10.0.1.1
#   r1-eth0  10.0.1.1/24    r1-eth1  10.0.12.1/24
#   r2-eth0  10.0.12.2/24   r2-eth1  10.0.23.2/24
#   r3-eth0  10.0.23.3/24   r3-eth1  10.0.3.1/24
#   h2-eth0  10.0.3.22/24   default via 10.0.3.1
#
# usage:
#   sudo /usr/bin/python3 exp2_topo.py

import os
import time

from mininet.topo import Topo
from mininet.net import Mininet

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_SH = os.path.abspath(os.path.join(HERE, os.pardir, 'report', 'exp2_test.sh'))

NODES = ('h1', 'h2', 'r1', 'r2', 'r3')
ROUTERS = ('r1', 'r2', 'r3')

# address of every port: {node: {interface: address}}
ADDR = {
    'h1': {'h1-eth0': '10.0.1.11/24'},
    'r1': {'r1-eth0': '10.0.1.1/24',  'r1-eth1': '10.0.12.1/24'},
    'r2': {'r2-eth0': '10.0.12.2/24', 'r2-eth1': '10.0.23.2/24'},
    'r3': {'r3-eth0': '10.0.23.3/24', 'r3-eth1': '10.0.3.1/24'},
    'h2': {'h2-eth0': '10.0.3.22/24'},
}

# static routes configured by hand on the routers: {node: [(dest, gateway), ...]}
ROUTES = {
    'r1': [('10.0.3.0/24',  '10.0.12.2'), ('10.0.23.0/24', '10.0.12.2')],
    'r2': [('10.0.1.0/24',  '10.0.12.1'), ('10.0.3.0/24',  '10.0.23.3')],
    'r3': [('10.0.1.0/24',  '10.0.23.2'), ('10.0.12.0/24', '10.0.23.2')],
}

# default routes on the two terminal hosts
DEFAULT_GW = {'h1': '10.0.1.1', 'h2': '10.0.3.1'}


class Exp2Topo(Topo):
    def build(self):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        r1 = self.addHost('r1')
        r2 = self.addHost('r2')
        r3 = self.addHost('r3')

        # h1 - r1 - r2 - r3 - h2
        self.addLink(h1, r1)
        self.addLink(r1, r2)
        self.addLink(r2, r3)
        self.addLink(r3, h2)


def config_addresses(net):
    """Assign an address to every port and bring it up."""
    for name in NODES:
        node = net.get(name)
        for iface, addr in sorted(ADDR[name].items()):
            node.cmd('ip addr flush dev %s' % iface)
            node.cmd('ip link set %s up' % iface)
            node.cmd('ip addr add %s dev %s' % (addr, iface))


def config_routes(net):
    """Manually configured router routing tables + terminal default routes."""
    for name in ROUTERS:
        node = net.get(name)
        for dest, gw in ROUTES[name]:
            node.cmd('ip route add %s via %s' % (dest, gw))

    for name, gw in DEFAULT_GW.items():
        net.get(name).cmd('ip route add default via %s' % gw)


def run_prescripts(net):
    """Disable the kernel forwarding/ARP/ICMP/offload so the user stack owns it."""
    for name in NODES:
        node = net.get(name)
        node.cmd('cd %s && ./scripts/disable_offloading.sh' % HERE)
        node.cmd('cd %s && ./scripts/disable_ipv6.sh' % HERE)

    for name in ROUTERS:
        node = net.get(name)
        for script in ('disable_arp.sh', 'disable_icmp.sh', 'disable_ip_forward.sh'):
            node.cmd('cd %s && ./scripts/%s' % (HERE, script))


def start_routers(net):
    for name in ROUTERS:
        net.get(name).cmd('cd %s && ./router >/tmp/%s-router.log 2>&1 </dev/null &'
                          % (HERE, name))
    time.sleep(1)


def main():
    net = Mininet(topo=Exp2Topo(), controller=None)
    net.start()

    config_addresses(net)
    config_routes(net)
    run_prescripts(net)
    start_routers(net)

    print('[started] addresses configured on all ports, pre-run scripts executed, '
          'routers running on r1/r2/r3')
    print()

    for name in ('h1', 'h2'):
        print(net.get(name).cmd('bash %s %s 2>&1' % (TEST_SH, name)).rstrip())
        print()

    net.get('r1').cmd('pkill -x router')
    net.stop()


if __name__ == '__main__':
    main()

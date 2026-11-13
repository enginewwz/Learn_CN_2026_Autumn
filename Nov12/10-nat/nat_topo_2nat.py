#!/usr/bin/env python3

"""
Exp 10.3: two-NAT topology

    h1 ---- n1(SNAT) ---- n2(DNAT) ---- h2

Addresses:
    h1: 10.21.0.1/16        default gw 10.21.0.254
    n1: 10.21.0.254/16      (internal, n1-eth0)
        159.226.39.43/24    (external, n1-eth1)
    n2: 159.226.39.44/24    (external, n2-eth0)
        10.22.0.254/16      (internal, n2-eth1)
    h2: 10.22.0.1/16        default gw 10.22.0.254

Run:
    n1# ./nat exp3-snat.conf
    n2# ./nat exp3-dnat.conf
    h2# python3 ./http_server.py
    h1# wget http://159.226.39.44:8000
"""

import os
import sys
import glob

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI

script_deps = ['ethtool', 'arptables', 'iptables']


def check_scripts():
    dir = os.path.abspath(os.path.dirname(sys.argv[0]))

    for fname in glob.glob(dir + '/' + 'scripts/*.sh'):
        if not os.access(fname, os.X_OK):
            print('%s should be set executable by using `chmod +x $script_name`' % fname)
            sys.exit(1)

    for program in script_deps:
        found = False
        for path in os.environ['PATH'].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
                found = True
                break
        if not found:
            print('`%s` is required but missing, which could be installed via `apt` or `aptitude`' % program)
            sys.exit(2)


class DoubleNATTopo(Topo):
    def build(self):
        h1 = self.addHost('h1')
        n1 = self.addHost('n1')
        n2 = self.addHost('n2')
        h2 = self.addHost('h2')

        # h1 -- n1 -- n2 -- h2
        self.addLink(h1, n1)
        self.addLink(n1, n2)
        self.addLink(n2, h2)


if __name__ == '__main__':
    check_scripts()

    topo = DoubleNATTopo()
    net = Mininet(topo=topo, controller=None)
    net.start()

    h1, n1, n2, h2 = net.get('h1', 'n1', 'n2', 'h2')

    # private network behind n1
    h1.cmd('ifconfig h1-eth0 10.21.0.1/16')
    h1.cmd('route add default gw 10.21.0.254')

    n1.cmd('ifconfig n1-eth0 10.21.0.254/16')
    n1.cmd('ifconfig n1-eth1 159.226.39.43/24')

    # public network between n1 and n2
    n2.cmd('ifconfig n2-eth0 159.226.39.44/24')

    # private network behind n2
    n2.cmd('ifconfig n2-eth1 10.22.0.254/16')

    h2.cmd('ifconfig h2-eth0 10.22.0.1/16')
    h2.cmd('route add default gw 10.22.0.254')

    for h in (h1, h2):
        h.cmd('./scripts/disable_offloading.sh')
        h.cmd('./scripts/disable_ipv6.sh')

    for n in (n1, n2):
        n.cmd('./scripts/disable_arp.sh')
        n.cmd('./scripts/disable_icmp.sh')
        n.cmd('./scripts/disable_ip_forward.sh')
        n.cmd('./scripts/disable_ipv6.sh')

    print('')
    print('n1# ./nat exp3-snat.conf')
    print('n2# ./nat exp3-dnat.conf')
    print('h2# python3 ./http_server.py')
    print('h1# wget http://159.226.39.44:8000')
    print('')

    CLI(net)
    net.stop()

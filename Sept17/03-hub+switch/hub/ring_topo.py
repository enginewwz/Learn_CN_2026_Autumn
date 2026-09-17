#!/usr/bin/python

import os
import sys
import glob

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.cli import CLI

script_deps = [ 'ethtool' ]

def check_scripts():
    dir = os.path.abspath(os.path.dirname(sys.argv[0]))

    for fname in glob.glob(dir + '/' + 'scripts/*.sh'):
        if not os.access(fname, os.X_OK):
            print('%s should be set executable by using `chmod +x $script_name`' % (fname))
            sys.exit(1)

    for program in script_deps:
        found = False
        for path in os.environ['PATH'].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
                found = True
                break
        if not found:
            print('`%s` is required but missing, which could be installed via `apt` or `aptitude`' % (program))
            sys.exit(2)

# Mininet will assign an IP address for each interface of a node 
# automatically, but hub or switch does not need IP address.
def clearIP(n):
    for iface in n.intfList():
        n.cmd('ifconfig %s 0.0.0.0' % (iface))

#            h1 --- b1 --- b2 --- h2
#                    \     /
#                     \   /
#                      b3
#
# b1 和 b2 之间有两条路：直连的 b1--b2，以及绕行的 b1--b3--b2。
# 三台 hub 一起跑起来之后，一个广播帧会沿着 b1->b2->b3->b1 永远转圈，
# 而且每经过一个 hub 就被复制一份。
class RingTopo(Topo):
    def build(self):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        b1 = self.addHost('b1')
        b2 = self.addHost('b2')
        b3 = self.addHost('b3')

        self.addLink(h1, b1, bw=20)     # h1-eth0  <-> b1-eth0
        self.addLink(b1, b2, bw=10)     # b1-eth1  <-> b2-eth0
        self.addLink(b2, h2, bw=20)     # b2-eth1  <-> h2-eth0
        self.addLink(b1, b3, bw=10)     # b1-eth2  <-> b3-eth0   ┐
        self.addLink(b3, b2, bw=10)     # b3-eth1  <-> b2-eth2   ┘ 环路: 绕行那条
        # 最终接口分布:
        #   b1: eth0->h1    eth1->b2    eth2->b3
        #   b2: eth0->b1    eth1->h2    eth2->b3
        #   b3: eth0->b1    eth1->b2

if __name__ == '__main__':
    check_scripts()

    topo = RingTopo()
    net = Mininet(topo = topo, link = TCLink, controller = None) 

    h1, h2, b1, b2, b3 = net.get('h1', 'h2', 'b1', 'b2', 'b3')
    h1.cmd('ifconfig h1-eth0 10.0.0.1/8')
    h2.cmd('ifconfig h2-eth0 10.0.0.2/8')

    # 集线器不需要 IP
    for b in [ b1, b2, b3 ]:
        clearIP(b)

    # Mininet hands out addresses in the order hosts are added, and it adds
    # them sorted by name: [b1, b2, b3, h1, h2].  So b1 is given 10.0.0.1 and
    # every other host is shifted (h1 -> .4, h2 -> .5).  The ifconfig/clearIP
    # calls above only change the kernel; Mininet's own cached Intf.ip keeps
    # that shifted assignment, and `pingall` pings dest.IP() -- i.e. the cache
    # -- so it would test the wrong hosts.  Re-read the addresses from the
    # interfaces so the cache matches reality: h1 -> .1, h2 -> .2, b1/b2/b3 -> None.
    for h in [ h1, h2, b1, b2, b3 ]:
        for iface in h.intfList():
            iface.updateIP()

    for h in [ h1, h2, b1, b2, b3 ]:
        h.cmd('./scripts/disable_offloading.sh')
        h.cmd('./scripts/disable_ipv6.sh')

    net.start()
    CLI(net)
    net.stop()

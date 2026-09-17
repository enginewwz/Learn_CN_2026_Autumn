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

class BroadcastTopo(Topo):
    def build(self):
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        b1 = self.addHost('b1')

        self.addLink(h1, b1, bw=20)
        self.addLink(h2, b1, bw=10)
        self.addLink(h3, b1, bw=10)

if __name__ == '__main__':
    check_scripts()

    topo = BroadcastTopo()
    net = Mininet(topo = topo, link = TCLink, controller = None) 

    h1, h2, h3, b1 = net.get('h1', 'h2', 'h3', 'b1')
    h1.cmd('ifconfig h1-eth0 10.0.0.1/8')
    h2.cmd('ifconfig h2-eth0 10.0.0.2/8')
    h3.cmd('ifconfig h3-eth0 10.0.0.3/8')
    clearIP(b1)

    # Mininet hands out addresses in the order hosts are added, and it adds
    # them sorted by name: [b1, h1, h2, h3].  So b1 is given 10.0.0.1 and
    # every other host is shifted by one (h1 -> .2, h2 -> .3, h3 -> .4).
    # The ifconfig/clearIP calls above only change the kernel; Mininet's own
    # cached Intf.ip keeps that shifted assignment, and `pingall` pings
    # dest.IP() -- i.e. the cache -- so it would test the wrong hosts
    # (e.g. it would "ping b1" by pinging 10.0.0.1, which is really h1 pinging
    # itself).  Re-read the addresses from the interfaces so the cache matches
    # reality: h1/h2/h3 -> .1/.2/.3, b1 -> None.
    for h in [ h1, h2, h3, b1 ]:
        for iface in h.intfList():
            iface.updateIP()

    for h in [ h1, h2, h3, b1 ]:
        h.cmd('./scripts/disable_offloading.sh')
        h.cmd('./scripts/disable_ipv6.sh')

    net.start()
    CLI(net)
    net.stop()

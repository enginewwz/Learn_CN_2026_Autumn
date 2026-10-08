#!/usr/bin/python

import os
import sys
import glob

from mininet.topo import Topo
from mininet.net import Mininet
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

def clearIP(n):
    for iface in n.intfList():
        n.cmd('ifconfig %s 0.0.0.0' % (iface))

# 7 个节点, 9 条链路 -> 6 条成树 + 3 条冗余 (要求 >= 2)
#
# 邻接表:
#   b1: b2 b3 b4
#   b2: b1 b3 b5
#   b3: b1 b2
#   b4: b1 b5 b6 b7
#   b5: b4 b6 b2
#   b6: b4 b5
#   b7: b4
#
#   形状: b1-b2-b3 三角 + b4-b5-b6 三角, 两个三角由 b1-b4 和 b2-b5 相连, b7 挂在 b4 下
#
# 链路(按 addLink 顺序, 决定每个节点 eth 的编号):
#   b1-b2  b2-b3  b3-b1  b1-b4  b4-b5  b5-b6  b6-b4  b4-b7  b2-b5
#
# 冗余链路 3 条: b3-b1(三角 b1b2b3), b6-b4(三角 b4b5b6), b2-b5(跨三角)
#
# 预期结果 (path_cost 全为 1, 根 = bridge id 最小的 b1):
#   b1  ROOT        cost=0  eth0->b2 DESIGNATED  eth1->b3 DESIGNATED  eth2->b4 DESIGNATED
#   b2  non-root    cost=1  eth0->b1 ROOT        eth1->b3 DESIGNATED  eth2->b5 DESIGNATED
#   b3  non-root    cost=1  eth0->b2 ALTERNATE   eth1->b1 ROOT
#   b4  non-root    cost=1  eth0->b1 ROOT        eth1->b5 DESIGNATED  eth2->b6 DESIGNATED  eth3->b7 DESIGNATED
#   b5  non-root    cost=2  eth0->b4 ALTERNATE   eth1->b6 DESIGNATED  eth2->b2 ROOT
#   b6  non-root    cost=2  eth0->b5 ALTERNATE   eth1->b4 ROOT
#   b7  non-root    cost=2  eth0->b4 ROOT
# 被阻塞(ALTERNATE)的 3 条链路: b3-b2, b5-b4, b6-b5 (每处只有一端 ALTERNATE)

class SevenNodeMeshTopo(Topo):
    def build(self):
        b1 = self.addHost('b1')
        b2 = self.addHost('b2')
        b3 = self.addHost('b3')
        b4 = self.addHost('b4')
        b5 = self.addHost('b5')
        b6 = self.addHost('b6')
        b7 = self.addHost('b7')

        self.addLink(b1, b2)        # 三角 b1-b2-b3
        self.addLink(b2, b3)
        self.addLink(b3, b1)
        self.addLink(b1, b4)        # b1 与 b4 相连
        self.addLink(b4, b5)        # 三角 b4-b5-b6
        self.addLink(b5, b6)
        self.addLink(b6, b4)
        self.addLink(b4, b7)        # b7 挂在 b4 上
        self.addLink(b2, b5)        # 跨两个三角的冗余链路

if __name__ == '__main__':
    check_scripts()

    topo = SevenNodeMeshTopo()
    net = Mininet(topo = topo, controller = None) 

    for idx in range(7):
        name = 'b' + str(idx+1)
        node = net.get(name)
        clearIP(node)
        node.cmd('./scripts/disable_offloading.sh')
        node.cmd('./scripts/disable_ipv6.sh')

        # set mac address for each interface
        for port in range(len(node.intfList())):
            intf = '%s-eth%d' % (name, port)
            mac = '00:00:00:00:0%d:0%d' % (idx+1, port+1)

            node.setMAC(mac, intf = intf)

        node.cmd('./stp > %s-output.txt 2>&1 &' % name)
        # node.cmd('./stp-reference > %s-output.txt 2>&1 &' % name)

    net.start()
    CLI(net)
    net.stop()

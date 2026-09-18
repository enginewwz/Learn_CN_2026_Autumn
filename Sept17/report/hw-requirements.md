# 03-交换机转发实验 实验要求

## 实验内容一(hub)

- 实现节点广播的broadcast_packet函数
- 验证广播网络能够正常运行
    - 从一个端节点ping另一个端节点
- 验证广播网络的效率
    - 在three_nodes_bw.py进行iperf测量
    - 两种场景：
        - H1: iperf client; H2, H3: servers （h1同时向h2和h3测量）
        - H1: iperf server; H2, H3: clients （ h2和h3 同时向h1测量）
- 自己动手构建环形拓扑，验证该拓扑下节点广播会产生数据包环路

## 实验内容二(switch)

- 实现对数据结构mac_port_map的所有操作，以及数据包的转发和广播操作
    - iface_info_t *lookup_port(u8 mac[ETH_ALEN]);
    - void insert_mac_port(u8 mac[ETH_ALEN], iface_info_t *iface);
    - int sweep_aged_mac_port_entry();
    - void broadcast_packet(iface_info_t *iface, const char *packet, int len);
    - void handle_packet(iface_info_t *iface, char *packet, int len);
- 使用iperf和给定的拓扑进行实验，对比交换机转发与集线器广播的性能

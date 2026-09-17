# 集线器实验结果
## 检查连通性

```bash
mininet> xterm b1
mininet> pingall
*** Ping: testing ping reachability
b1 -> X X X 
h1 -> *** Error: could not parse ping output: ping: None: Temporary failure in name resolution

X h2 h3 
h2 -> *** Error: could not parse ping output: ping: None: Temporary failure in name resolution

X h1 h3 
h3 -> *** Error: could not parse ping output: ping: None: Temporary failure in name resolution

X h1 h2 
*** Results: 50% dropped (6/12 received)
mininet> h1 ping h2 -c 2
PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.
64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.060 ms
64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.344 ms

--- 10.0.0.2 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1123ms
rtt min/avg/max/mdev = 0.060/0.202/0.344/0.142 ms
mininet> h1 ping h3 -c 2
PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.
64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=0.132 ms
64 bytes from 10.0.0.3: icmp_seq=2 ttl=64 time=0.093 ms

--- 10.0.0.3 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1113ms
rtt min/avg/max/mdev = 0.093/0.112/0.132/0.019 ms
mininet> h3 ping h2 -c 2
PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.
64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.054 ms
64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.070 ms

--- 10.0.0.2 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1110ms
rtt min/avg/max/mdev = 0.054/0.062/0.070/0.008 ms
```

## 性能测试
h1 向 h2 h3 发送(h1)：
```bash
iperf -c 10.0.0.2 -t 30 & iperf -c 10.0.0.3 -t 30
[1] 61416
------------------------------------------------------------
Client connecting to 10.0.0.3, TCP port 5001
Client connecting to 10.0.0.2, TCP port 5001
TCP window size: 85.3 KByte (default)TCP window size: 85.3 KByte (default)
------------------------------------------------------------
------------------------------------------------------------
[  1] local 10.0.0.1 port 52310 connected with 10.0.0.3 port 5001 (icwnd/mss/irtt=14/1448/253)
[  1] local 10.0.0.1 port 42300 connected with 10.0.0.2 port 5001 (icwnd/mss/irtt=14/1448/300)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-30.4909 sec  21.3 MBytes  5.85 Mbits/sec
root@LAPTOP-5NFK39JP:/home/enginew/CN_lab_2026_Autumn/Sept17/03-hub+switch/hub#  [ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-30.8921 sec  11.0 MBytes  2.99 Mbits/sec
```

h2 h3 发送给 h1(几乎同时):
```bash
iperf -s
------------------------------------------------------------
Server listening on TCP port 5001
TCP window size: 85.3 KByte (default)
------------------------------------------------------------
[  1] local 10.0.0.1 port 5001 connected with 10.0.0.3 port 35392 (icwnd/mss/irtt=14/1448/140)
[  2] local 10.0.0.1 port 5001 connected with 10.0.0.2 port 44322 (icwnd/mss/irtt=14/1448/21686)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-30.8588 sec  30.4 MBytes  8.26 Mbits/sec
[  2] 0.0000-30.6453 sec  30.6 MBytes  8.38 Mbits/sec
```


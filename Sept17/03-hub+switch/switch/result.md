# 交换机实验部分

## 连通性测试

```bash
mininet> pingall
*** Ping: testing ping reachability
h1 -> h2 h3 X 
h2 -> h1 h3 X 
h3 -> h1 h2 X 
s1 -> X X X 
*** Results: 50% dropped (6/12 received)
```

## 性能测试

```bash
iperf -c 10.0.0.2 -t 30 & iperf -c 10.0.0.3 -t 30
[1] 43097
------------------------------------------------------------
Client connecting to 10.0.0.2, TCP port 5001
TCP window size: 85.3 KByte (default)
------------------------------------------------------------
------------------------------------------------------------
Client connecting to 10.0.0.3, TCP port 5001
TCP window size: 85.3 KByte (default)
------------------------------------------------------------
[  1] local 10.0.0.1 port 51462 connected with 10.0.0.2 port 5001 (icwnd/mss/irtt=14/1448/166)
[  1] local 10.0.0.1 port 55418 connected with 10.0.0.3 port 5001 (icwnd/mss/irtt=14/1448/118)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-31.0876 sec  34.8 MBytes  9.38 Mbits/sec
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-31.2286 sec  35.0 MBytes  9.40 Mbits/sec
[1]+  Done                    iperf -c 10.0.0.2 -t 30
```

```bash
iperf -s
------------------------------------------------------------
Server listening on TCP port 5001
TCP window size: 85.3 KByte (default)
------------------------------------------------------------
[  1] local 10.0.0.1 port 5001 connected with 10.0.0.2 port 42756 (icwnd/mss/irtt=14/1448/28)
[  2] local 10.0.0.1 port 5001 connected with 10.0.0.3 port 39966 (icwnd/mss/irtt=14/1448/49)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-31.0278 sec  34.4 MBytes  9.29 Mbits/sec
[  2] 0.0000-30.9100 sec  34.3 MBytes  9.30 Mbits/sec
```
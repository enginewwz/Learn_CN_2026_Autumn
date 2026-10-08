# 04 生成树实验报告：STP 生成树机制的用户态实现

| 项目 | 内容 |
| --- | --- |
| 姓名 | 吴峥 |
| 学号 | 2024K8009909013 |
| 班级 | 2406 |
| 实验日期 | 2026 年 9 月 24 日 |

## 一、实验目的与完成情况

本实验在给定的用户态二层转发框架上实现生成树协议（Spanning Tree Protocol，STP），使多台交换机能在存在冗余链路的网络中自动协商出一棵无环的转发树。实验要求分为两部分：

1. 基于已有代码实现生成树运行机制，对给定的四节点环形拓扑 `four_node_ring.py` 计算并输出对应状态下的最小生成树；
2. 自行构造一个不少于 7 个节点、冗余链路不少于 2 条的拓扑，节点与端口命名参考 `four_node_ring.py`，用同一个程序计算并输出最小生成树。

需要补写的部分集中在 `stp.c`：入口是 `stp_handle_config_packet()`，并由它调用若干辅助函数完成配置报文解码、优先级向量比较、根端口与指定端口的重新选举。

## 二、实验环境与代码框架

### 2.1 实验环境

| 组成 | 本实验使用的配置 |
| --- | --- |
| 运行环境 | WSL 2（Windows Subsystem for Linux 2） |
| Linux 发行版 | Ubuntu 24.04 LTS |
| 编程语言与编译工具 | C、GCC、Make |
| 网络虚拟化 | Mininet 2.3.0 |

### 2.2 代码结构与数据流

框架由四个源文件和两组头文件组成，STP 协议逻辑全部集中在 `stp.c`：

| 文件 | 职责 |
| --- | --- |
| `main.c` | 程序入口、`poll()` 收包循环与 `handle_packet()` |
| `device_internal.c` | 原始套接字层：发现 `*-eth*` 接口、收发以太帧 |
| `stp_timer.c` | 通用定时器链表与计时函数，1 个 tick 为 1/256 秒 |
| `stp.c` | STP 协议逻辑，本实验的修改全部在这里 |
| `include/stp.h` | `stp_t`、`stp_port_t` 等数据结构 |
| `include/stp_proto.h` | BPDU 报文格式、桥/端口优先级与时间常量 |

程序启动后，`init_ustack()` 为每个 `*-eth*` 接口打开一个原始套接字，`stp_init()` 为每个接口建立一个 STP 端口并启动 hello 定时器，随后主线程进入 `ustack_run()` 的 `poll()` 循环。数据流可以概括为：

```text
以太网收帧
  └─ ustack_run() / poll()                          main.c
       └─ handle_packet()                           main.c
            └─ 目的 MAC 为 01:80:C2:00:00:01 时交给
               stp_port_handle_packet()             stp.c（加锁并按时分报文类型分发）
                    └─ stp_handle_config_packet()   stp.c（本实验补写）

定时器线程
  └─ stp_timer_run_once()                           stp_timer.c
       └─ stp_handle_hello_timeout()                stp.c（每 2 秒向所有指定端口发 BPDU）

进程收到 SIGTERM
  └─ stp_dump_state()                               stp.c（打印根信息与各端口角色后退出）
```

发送方向由 `stp_send_config()` 统一处理：它遍历所有端口，只让**指定端口（DESIGNATED）**调用 `stp_port_send_config()` 组包，再经 `stp_port_send_packet()` 加上以太头与 LLC/SNAP 头后发出。STP 的 BPDU 使用组播地址 `01:80:C2:00:00:01`，因此 `handle_packet()` 只处理目的 MAC 等于该地址的帧，其余数据帧直接丢弃。

### 2.3 数据结构与 BPDU 报文

每个端口用 `stp_port_t` 描述，其中最关键的是四个 `designated_*` 字段：

```c
struct stp_port {
	stp_t *stp;                 // 指回所属交换机
	int port_id;                // 端口 ID，形如 0x8001、0x8002
	char *port_name;
	iface_info_t *iface;        // 对应的网络接口
	int path_cost;              // 本端口的链路代价，本实验恒为 1

	u64 designated_root;        // 该端口所在网段上认定的根桥
	u64 designated_switch;      // 发送当前最优 BPDU 的交换机
	int designated_port;        // 发送当前最优 BPDU 的端口
	int designated_cost;        // 该 BPDU 中携带的到根开销
};
```

这四个字段表示的是**该端口所连网段上目前已知的最优向量**：如果这个向量由本机自己发出，那么 `designated_switch == switch_id` 且 `designated_port == port_id`，该端口就是指定端口。因此程序并不单独保存“端口角色”信息，而是在需要时由这两个条件推导：

```c
static const char *stp_port_state(stp_port_t *p)
{
	if (p->stp->root_port && p->port_id == p->stp->root_port->port_id)
		return "ROOT";
	else if (p->designated_switch == p->stp->switch_id &&
		p->designated_port == p->port_id)
		return "DESIGNATED";
	else
		return "ALTERNATE";
}
```

交换机级的 `stp_t` 则保存本机当前的视图：`designated_root`（本机认定的根桥）、`root_path_cost`（本机到根的开销）、`root_port`（通向根的端口）以及 hello 定时器。桥 ID `switch_id` 由桥优先级与接口 MAC 拼接而成，网桥优先级取 32768，端口优先级取 128，因此所有节点的桥 ID 只由 MAC 决定，MAC 最小的 `b1` 天然成为根桥。

STP 配置报文（BPDU）沿用以太网帧格式，结构如下：

```c
struct stp_config {
	struct stp_header header;   // 协议 ID、版本、报文类型（CONFIG）
	u8 flags;                   // 本实验恒为 0
	u64 root_id;                // 发送方认定的根桥 ID
	u32 root_path_cost;         // 发送方到根的开销
	u64 switch_id;              // 发送方自己的桥 ID
	u16 port_id;                // 发送方发出该报文的端口 ID
	u16 msg_age;                // 报文年龄，本实验未使用
	u16 max_age;                // 根信息超时，本实验未使用
	u16 hello_time;             // BPDU 发送周期，取 STP_HELLO_TIME
	u16 fwd_delay;              // 状态迁移延时，本实验未使用
};
```

所有多字节字段在线路上都是网络字节序，取值时必须转换。时间以一个 tick 为 1/256 秒计，hello 周期 `STP_HELLO_TIME` 换算后为 2 秒。本实验不实现端口状态机（blocking / listening / learning），也不做 `msg_age` 老化，端口只有 ROOT、DESIGNATED、ALTERNATE 三种角色。

## 三、STP 机制的设计与实现

STP 的核心是让每台交换机不断向邻居交换“我认为的根是谁、我离根多远、我是谁、我用哪个端口发的”这组信息，并始终保留其中最优的一份，最终所有交换机对同一个根达成一致，且每个网段只有一台指定桥。

### 3.1 优先级向量与字典序比较

为便于比较，实现中把参与比较的四项组织成一个向量：

```c
typedef struct {
	uint64_t designated_root;   // 根桥 ID
	uint32_t root_path_cost;    // 到根开销
	uint64_t designated_switch; // 指定桥 ID
	uint16_t designated_port;   // 指定端口 ID
} stp_priority_vector_t;
```

比较规则是字典序，**数值越小越优**：

```c
static bool stp_vector_better(const stp_priority_vector_t *a,
                              const stp_priority_vector_t *b)
{
	if (a->designated_root   != b->designated_root)   return a->designated_root   < b->designated_root;
	if (a->root_path_cost    != b->root_path_cost)    return a->root_path_cost    < b->root_path_cost;
	if (a->designated_switch != b->designated_switch) return a->designated_switch < b->designated_switch;
	return a->designated_port < b->designated_port;
}
```

先比根桥 ID，是为了让全网统一到 MAC 最小的那台交换机；根相同时比到根开销；开销也相同时比指定桥 ID 与端口 ID，用于在代价并列时给出唯一解。最后两项保证比较结果构成全序，保证任何两条信息都能分出优劣。

### 3.2 两种代价：线上宣告值与本机累计值

实现中最容易出错的一点是“链路代价加在哪”，这里需要区分两个不同的量：

| 量 | 含义 | 存放位置 |
| --- | --- | --- |
| 宣告值 | 对端 BPDU 里写的、它自己到根的开销 | `p->designated_cost` |
| 累计值 | 本机经过某个端口到根的开销 | `stp->root_path_cost` |

阻塞端口和根端口储存的 cost 信息不包含 path_cost，只有 stp 的 cost 字段以及直接根据它分发的指定端口的 cost 信息包含了根端口对应的 path_cost。

```c
// 解码时按原值保存，不做任何加法
static inline stp_priority_vector_t stp_decode_config(struct stp_config *config)
{
	stp_priority_vector_t v;
	v.designated_root   = ntohll(config->root_id);
	v.root_path_cost    = ntohl(config->root_path_cost);
	v.designated_switch = ntohll(config->switch_id);
	v.designated_port   = ntohs(config->port_id);
	return v;
}
```

```c
// 选出根端口之后，才把本端口的链路代价加进交换机本身的代价字段
stp->root_path_cost = root->designated_cost + root->path_cost;
```

阻塞端口处代价字段不计入 path_cost 是基于以下原因：

我们希望一个网段的首尾两个接口互相发送 config 信息时，各自处理得到一致的大小比较结果（否则会出现两个端口只对外来信号加代价的不对称情景），这样两者必定能选出唯一胜者。另一方面，我们需要确定某次 config 信息是否有更低的代价，从而导致比如同一个端口走不同链路的可能性，这也需要端口保存原始信息。至于链路的开销同一由 stp 这一层面处理。

### 3.3 收到 BPDU 的处理

`stp_handle_config_packet()` 是本次补写的入口，它把收到的向量与**本端口已经记录的最优向量**作比较，只做两种反应：

```c
static void stp_handle_config_packet(stp_t *stp, stp_port_t *p, struct stp_config *config)
{
	bool was_root = stp_is_root_switch(stp);
	stp_priority_vector_t recv = stp_decode_config(config);
	stp_priority_vector_t seg  = stp_port_vector(p);

	if (stp_vector_better(&recv, &seg))
	{
		// 收到更优的向量：本端口让出指定端口身份，记录新的网段最优
		p->designated_root   = recv.designated_root;
		p->designated_cost   = recv.root_path_cost;
		p->designated_switch = recv.designated_switch;
		p->designated_port   = recv.designated_port;

		stp_update_state(stp);                  // 重新计算根端口与各端口角色
		if (was_root && !stp_is_root_switch(stp))
			stp_stop_timer(&stp->hello_timer);  // 自己不再是根，停止发送 hello
		stp_send_config(stp);                   // 状态已变，立即重新公告
	}
	else if (stp_port_is_designated(p))
	{
		// 收到的信息不比自己好：本机信息更优，回送一份让对方更新
		stp_port_send_config(p);
	}
}
```

这里有两个设计要点。第一，收到更优信息时本端口**让位**：用对端的向量覆盖自己的 `designated_*`，于是该端口不再满足“指定端口”的条件，随后由 `stp_update_state()` 决定它究竟成为根端口还是 ALTERNATE。第二，收到不优信息时本端口不做任何修改，只回送自己的 BPDU——因为本端口保存的就是该网段的最优向量，需要更新的是对端。这一机制在启动阶段尤其重要：此时每台交换机都以为自己是根，所有端口都是指定端口，双方互收对方 BPDU 后，桥 ID 更小的一方保持指定端口并回送，另一方收到更优信息后让位，一轮即可分出胜负。

### 3.4 全局状态重算

根端口的选择不能只看“刚收到报文的那一个端口”，而要在**所有非指定端口**中挑出最优的一个，因为它们各自记录着所在网段的最优信息：

```c
static void stp_update_state(stp_t *stp)
{
	stp_port_t *root = NULL;
	for (int i = 0; i < stp->nports; i++)
	{
		stp_port_t *p = &stp->ports[i];
		if (stp_port_is_designated(p)) continue;              // 指定端口代表本机自己的宣告，不参与
		if (p->designated_root >= stp->switch_id) continue;   // 只接受比自己更优的根
		if (!root) { root = p; continue; }
		if (stp_root_port_better(p, root)) root = p;
	}

	stp->root_port = root;

	if (!root) {                                              // 没有更优的根，本机即根桥
		stp->designated_root = stp->switch_id;
		stp->root_path_cost  = 0;
	} else {
		stp->designated_root = root->designated_root;
		stp->root_path_cost  = root->designated_cost + root->path_cost;
	}

	for (int i = 0; i < stp->nports; i++) {
		stp_port_t *p = &stp->ports[i];
		if (stp_port_is_designated(p)) { stp_port_set_designated(p); continue; }
		stp_priority_vector_t mine = stp_local_vector(p), seg = stp_port_vector(p);
		if (!stp_vector_better(&seg, &mine))                  // 本机向量不劣于网段最优
			stp_port_set_designated(p);
	}
}
```

其中根端口的比较函数把本端口的链路代价算了进去，因为它衡量的是“本机实际走哪条路到根更近”：

```c
static bool stp_root_port_better(stp_port_t *a, stp_port_t *b)
{
	if (a->designated_root != b->designated_root) return a->designated_root < b->designated_root;
	u32 ca = (u32)a->designated_cost + a->path_cost;
	u32 cb = (u32)b->designated_cost + b->path_cost;
	if (ca != cb) return ca < cb;
	if (a->designated_switch != b->designated_switch) return a->designated_switch < b->designated_switch;
	if (a->designated_port != b->designated_port) return a->designated_port < b->designated_port;
	return a->port_id < b->port_id;
}
```

循环中的 `if (p->designated_root >= stp->switch_id) continue;` 对特殊情况加了约束。根桥的定义是全网红桥 ID 最小的交换机，所以“本机不是根”的唯一合法依据是听到了一个严格比本机更小的根。当一个非指定端口记录的根恰好等于本机桥 ID（例如本机发出的 BPDU 经共享介质绕回、被另一个端口收到）时，如果不加这条判断，它会被选成根端口，于是本机同时出现 `designated_root == switch_id`（仍认为自己是根）与 `root_path_cost != 0`（又有根端口）的矛盾状态，并会把被抬高的开销公告给下游。加上这一行后，“我是根”与“我有根端口”互斥。

第二段循环负责刷新各端口保存的向量。本机视图变化后，`designated_root` 与 `root_path_cost` 都会改变，指定端口必须同步改成新的向量，否则它会继续对外宣告过期的根信息，下游永远收敛不了。对非指定端口，则用“本机候选向量是否不劣于网段最优向量”判断它是否应当成为指定端口；若判断为是，就把本机视图写入该端口，使其转为 DESIGNATED。

### 3.5 端口角色、定时器与收敛

综合以上过程，一个端口最终的角色可以这样理解：

| 条件 | 角色 |
| --- | --- |
| 端口保存的向量由本机自己发出 | DESIGNATED，需要周期性地发送 BPDU |
| 收到的向量不劣于本机，且该端口是所有非指定端口中到根最优的 | ROOT |
| 其余情况 | ALTERNATE，逻辑上被阻塞，不转发也不发送 BPDU |

换句话说，被阻塞的端口同时满足两件事：它没有赢得所在网段的指定桥选举，也不是本机通向根的最优上行端口。

hello 定时器周期为 2 秒，回调 `stp_handle_hello_timeout()` 只做两件事：向所有指定端口发送一次当前 BPDU，然后重新启动定时器。此外，为了加快收敛，程序在两处主动发送：收到更优 BPDU 并完成重算后，立即调用 `stp_send_config()` 把新状态广播给所有指定端口；收到不优 BPDU 时，由指定端口回送一份，使对端尽快更新。当交换机原本是根、在一次处理之后不再是根时，程序调用 `stp_stop_timer()` 停止 hello 定时器，避免旧根继续发送已经失效的 BPDU。

由于 `designated_cost` 保存的是线上宣告值，而指定端口的 `designated_cost` 是本机自己的累计值，状态转储中可以据此核对实现是否正确：根端口那一行显示的是邻居的宣告值，指定端口那一行显示的是本机到根的距离。

## 四、实验一：四节点环

### 4.1 拓扑与运行方式

实验一使用课程给定的 `four_node_ring.py`，四个节点首尾相连构成环，每台交换机有 2 个端口，链路代价均为 1：

```text
b1 ── b2
│      │
b3 ── b4
```

运行方式如下：

```bash
cd Sept24/04-stp
make
sudo python3 four_node_ring.py
```

在 Mininet 命令行中为每个节点启动程序，等待数秒收敛后向各节点的 `stp` 进程发送 `SIGTERM`，程序会打印状态并退出：

```text
mininet> b1 ./stp > b1-output.txt 2>&1 &
mininet> b2 ./stp > b2-output.txt 2>&1 &
mininet> b3 ./stp > b3-output.txt 2>&1 &
mininet> b4 ./stp > b4-output.txt 2>&1 &

(bash) sudo pkill -SIGTERM stp

mininet> exit
```

```bash
./dump_output.sh 4
```

### 4.2 运行结果

```text
NODE b1 dumps:
INFO: this switch is root.
INFO: port id: 01, role: DESIGNATED.
INFO:   designated ->root: 0101, ->switch: 0101, ->port: 01, ->cost: 0.
INFO: port id: 02, role: DESIGNATED.
INFO:   designated ->root: 0101, ->switch: 0101, ->port: 02, ->cost: 0.

NODE b2 dumps:
INFO: non-root switch, designated root: 0101, root path cost: 1.
INFO: port id: 01, role: ROOT.
INFO:   designated ->root: 0101, ->switch: 0101, ->port: 01, ->cost: 0.
INFO: port id: 02, role: DESIGNATED.
INFO:   designated ->root: 0101, ->switch: 0201, ->port: 02, ->cost: 1.

NODE b3 dumps:
INFO: non-root switch, designated root: 0101, root path cost: 1.
INFO: port id: 01, role: ROOT.
INFO:   designated ->root: 0101, ->switch: 0101, ->port: 02, ->cost: 0.
INFO: port id: 02, role: DESIGNATED.
INFO:   designated ->root: 0101, ->switch: 0301, ->port: 02, ->cost: 1.

NODE b4 dumps:
INFO: non-root switch, designated root: 0101, root path cost: 2.
INFO: port id: 01, role: ROOT.
INFO:   designated ->root: 0101, ->switch: 0201, ->port: 02, ->cost: 1.
INFO: port id: 02, role: ALTERNATE.
INFO:   designated ->root: 0101, ->switch: 0301, ->port: 02, ->cost: 1.
```

![四节点环收敛后的端口角色](result-graph1.png)

### 4.3 结果分析

四台交换机的网桥优先级相同，桥 ID 由 MAC 决定，`b1` 的 MAC 最小，因此 `b1` 被选为根桥，四个端口中它自己的两个端口都是指定端口，`root_path_cost` 为 0。`b2` 和 `b3` 各自从连接 `b1` 的端口收到开销为 0 的 BPDU，于是该端口成为根端口，自身到根开销为 1；它们的另一个端口赢得所在网段的指定桥选举，成为指定端口，对外宣告开销 1。

`b4` 从 `b2`、`b3` 两个方向都能收到开销为 1 的 BPDU，两条路到根的总代价都是 2，出现并列。按字典序继续比较第三项“指定桥 ID”，`b2` 的 0201 小于 `b3` 的 0301，因此 `b2` 方向的向量更优，成为 `b4` 的根端口；`b3` 方向的端口既没有赢下网段选举，也不是到根的最优上行口，被判为 ALTERNATE。最终环上有一条链路被逻辑阻塞，转发拓扑退化为 `b1-b2`、`b1-b3`、`b2-b4` 三条链路构成的树，恰好比原来少一条边。

## 五、实验二：七节点自建拓扑

### 5.1 拓扑设计

自建拓扑保存在 `seven_node_mesh.py`，共 7 个节点、9 条链路，其中 6 条构成生成树，另外 3 条为冗余链路，满足“不少于 2 条”的要求。邻接关系如下：

```text
b1: b2 b3 b4
b2: b1 b3 b5
b3: b1 b2
b4: b1 b5 b6 b7
b5: b4 b6 b2
b6: b4 b5
b7: b4
```

形状上，它由两个三角形（`b1-b2-b3` 与 `b4-b5-b6`）通过 `b1-b4` 和 `b2-b5` 相连，`b7` 挂在 `b4` 下面，其形状如下所示。9 条链路比 7 个节点成树所需的 6 条多出 3 条：`b2-b5` 把两个三角形连在一起，同时为 `b5` 提供了一条绕开 `b4` 的路径；两个三角形内部各有多余的链路，收敛后最终被阻塞的是 `b2-b3`、`b4-b5` 和 `b5-b6`。这样设计的原因有三点：三角形提供了“就近阻塞”的情形；跨三角形的链路提供了“远距离绕行”的情形；`b5` 到根有两条等长的路径，可以检验代价并列时按指定桥 ID 定序的逻辑。此外 `b1` 有 3 个端口、`b4` 有 4 个端口，也顺带检验了多端口的情况。

![七节点拓扑：蓝色实线为收敛后承载转发的链路，红色虚线为收敛后被阻塞的冗余链路](topology-seven-node.png)

拓扑文件沿用 `four_node_ring.py` 的写法：节点命名为 `b1` 到 `b7`，第 $i$ 台交换机的第 $j$ 个接口被设置为 `00:00:00:00:0i:0j`，因此桥 ID 依次递增，`b1` 是根桥。

### 5.2 运行结果

运行方式与实验一相同，只需把拓扑文件换成 `seven_node_mesh.py`，最后用 `./dump_output.sh 7` 汇总。把各节点的转储按端口与对端整理后如下表所示，其中“记录的向量”一列即该端口保存的 `designated_root / designated_switch / designated_port / designated_cost`：

| 节点 | 端口 | 对端 | 角色 | 记录的向量 |
| --- | ---: | --- | --- | --- |
| b1 | 01 | b2 | DESIGNATED | 0101 / 0101 / 01 / 0 |
| b1 | 02 | b4 | DESIGNATED | 0101 / 0101 / 02 / 0 |
| b1 | 03 | b3 | DESIGNATED | 0101 / 0101 / 03 / 0 |
| b2 | 01 | b1 | ROOT | 0101 / 0101 / 01 / 0 |
| b2 | 02 | b3 | DESIGNATED | 0101 / 0201 / 02 / 1 |
| b2 | 03 | b5 | DESIGNATED | 0101 / 0201 / 03 / 1 |
| b3 | 01 | b2 | ALTERNATE | 0101 / 0201 / 02 / 1 |
| b3 | 02 | b1 | ROOT | 0101 / 0101 / 03 / 0 |
| b4 | 01 | b1 | ROOT | 0101 / 0101 / 02 / 0 |
| b4 | 02 | b5 | DESIGNATED | 0101 / 0401 / 02 / 1 |
| b4 | 03 | b7 | DESIGNATED | 0101 / 0401 / 03 / 1 |
| b4 | 04 | b6 | DESIGNATED | 0101 / 0401 / 04 / 1 |
| b5 | 01 | b2 | ROOT | 0101 / 0201 / 03 / 1 |
| b5 | 02 | b4 | ALTERNATE | 0101 / 0401 / 02 / 1 |
| b5 | 03 | b6 | DESIGNATED | 0101 / 0503 / 03 / 2 |
| b6 | 01 | b5 | ALTERNATE | 0101 / 0503 / 03 / 2 |
| b6 | 02 | b4 | ROOT | 0101 / 0401 / 04 / 1 |
| b7 | 01 | b4 | ROOT | 0101 / 0401 / 03 / 1 |

![七节点拓扑收敛结果（上半部分）](result-graph2-upper.png)

![七节点拓扑收敛结果（下半部分）](result-graph2-lower.png)

### 5.3 结果分析

从序号看，所有节点认定的根都是 0101，即 `b1`；各节点到根的开销分别为 `b1` 为 0，`b2`、`b3`、`b4` 为 1，`b5`、`b6`、`b7` 为 2，正好等于它们在拓扑中到 `b1` 的最少跳数。9 条链路中有 6 条承载转发、3 条被阻塞，与 7 个节点需要 6 条边的树结构一致：

| 被阻塞的链路 | ALTERNATE 所在的一端 | 阻塞原因 |
| --- | --- | --- |
| `b2-b3` | b3 的端口 01 | 两者宣告开销同为 1，比较指定桥 ID 时 0201 小于 0301 |
| `b4-b5` | b5 的端口 02 | b5 到根的两条路都是 2 跳，比较指定桥 ID 时 0201 小于 0401 |
| `b5-b6` | b6 的端口 01 | 两者宣告开销同为 2，比较指定桥 ID 时 0503 小于 b6 的桥 ID |

三条冗余链路都只在**一端**变成 ALTERNATE，另一端仍然是 DESIGNATED，因此每条链路只由一端负责发送 BPDU，不会形成两个指定桥同时发言的情况。这也说明“指定桥选举”是在每个网段上独立进行的局部判断，而“根端口”是本机在多个候选端口中做的全局选择：`b5` 的两个非指定端口在各自网段上都输给了对端，其中到根更优的那一个（经 `b2`）成为根端口，另一个（经 `b4`）成为 ALTERNATE，`b4-b5` 这条链路因此被阻塞。

表中还出现了一个值得注意的细节：`b5` 的桥 ID 打印为 0503 而不是 0501。程序在 `stp_init()` 中取接口链表的第一个元素计算桥 ID，而接口链表的顺序取决于系统枚举接口的顺序，并不保证是 `eth0`、`eth1`、`eth2` 的顺序。因此转储里的端口编号与拓扑文件中 `addLink` 的顺序并不一一对应，需要借助对端记录的向量反推端口连接关系，上表的“对端”一列正是这样得到的。桥 ID 与端口编号的对应关系虽然会随枚举顺序变化，但每个节点的桥 ID 仍然保持在 `b1` 到 `b7` 递增，根桥与生成树的结果不受影响。

## 六、实验总结

本次实验在一个真实的用户态二层转发框架上实现了生成树协议的核心逻辑，主要包括三部分工作：解码并比较 BPDU 中的优先级向量、在收到更优信息时重算根端口与各端口角色、以及通过定时器周期性地公告并加速收敛。

实现过程中体会最深的是 STP 里“代价”这一概念的两种不同身份。报文里携带的是发送方**已经累计好的**到根开销，它属于发送方；而本机经过某个端口到达根的开销，等于对端宣告值再加上**本机这个端口的链路代价**。二者只差一次加法，但这次加法必须发生在交换机本身、且只发生一次。

实验结果表明，四节点环中 `b4` 的两个方向代价并列，程序按指定桥 ID 选择了经 `b2` 的路径，`b3` 方向的端口被阻塞；七节点拓扑中 9 条链路收敛出 6 条转发的生成树，三条冗余链路各自在一端被阻塞，各节点到根的开销与最少跳数完全一致。这说明所实现的状态机能够在存在多条冗余链路的网络中正确、稳定地收敛到无环拓扑。此外也认识到，STP 得到的是“以桥 ID 最小的交换机为根、相对于所配置代价而言的最短路径树”，它并不对真实网络的带宽或时延做任何测量，树的好坏完全取决于链路代价这一静态配置，这既是 STP 简洁可靠的原因，也是它在异构链路下可能不够优的根源。

# Socket 编程实验报告：HTTP/HTTPS 文件服务器

姓名：吴峥　　学号：2024K8009909013

## 一、实验目标与完成情况

本实验使用 C 语言和 Linux Socket 接口实现文件服务器，通过 OpenSSL 提供 HTTPS 通信，并使用非阻塞 I/O 与 `epoll` 管理多个客户端连接。服务器同时监听 HTTP 80 端口和 HTTPS 443 端口：HTTP 请求重定向到 HTTPS，HTTPS GET 请求处理，包含完成文件读取与响应，支持完整文件获取、文件不存在处理以及单段字节范围传输。

本实验已通过线上验证。

## 二、实验环境与整体结构

### 2.1 实验环境

| 组成 | 本实验使用的配置 |
| --- | --- |
| 运行环境 | WSL2 |
| 编程语言与编译工具 | C、GCC、Make |
| 网络与加密接口 | Linux Socket、`epoll`、OpenSSL |
| 实验网络 | Mininet，两个主机与一个 OVS 交换机 |
| 服务端 | `h1`，地址 `10.0.0.1`，监听 80、443 端口 |
| 客户端 | `h2`，地址 `10.0.0.2`，使用 Python Requests 发起样例请求 |

`topo.py` 中的网络拓扑为：

```text
h2（客户端，10.0.0.2）── s1 ── h1（服务器，10.0.0.1）
```

其中，`h1` 与 `s1` 之间的链路配置为 `bw=100`、`delay='10ms'`，即带宽参数为 100 Mbit/s、时延参数为 10 ms。

### 2.2 请求处理流程

HTTP 与 HTTPS 共用连接管理和响应发送框架，区别在于 HTTPS 需要先完成 TLS 握手，并通过 OpenSSL 读写加密数据。

```text
建立 TCP 连接
    ├─ HTTP：直接读取请求 ── 返回 301 与 HTTPS 地址
    └─ HTTPS：完成 TLS 握手 ── 读取请求
                                 ├─ 文件不存在：404
                                 ├─ 完整文件：200
                                 └─ 有效单段 Range：206
发送完响应后关闭连接并释放资源
```

每个连接独立保存请求缓冲区、响应缓冲区、发送进度、文件偏移和剩余文件长度。因此，一个连接等待网络数据时，其他已经就绪的连接仍可继续处理。

### 2.3 核心数据结构

程序使用两个结构体，分别表示一次请求的解析结果和一个连接的完整处理上下文，定义如下：

```c
struct http_request
{
	char *method;
	char *target;
	char *range;
	int if_range;
};
```

```c
struct connection
{
	int fd, tls;
	SSL *ssl;
	enum state state;
	char input[4096];
	char output[8192];
	size_t used, sent, output_len;
	/* output holds the response header, then successive file chunks. */
	int file_fd;
	off_t file_offset, file_remaining;
};
```

`http_request` 是 `build_response()` 内部的临时对象。`method`、`target` 和 `range` 都指向输入缓冲区中已切分的字符串；`if_range` 记录请求是否包含 `If-Range` 条件。本实现不验证该条件对应的资源版本，而是回退为完整文件响应。

`connection` 则跨多次事件保留，直到连接结束才释放。各组字段分工如下：

| 字段 | 作用 |
| --- | --- |
| `fd`、`tls`、`ssl` | 保存套接字、是否使用 TLS 以及对应的 OpenSSL 连接对象 |
| `state` | 记录当前处于握手、读取、发送或关闭阶段；监听对象使用 `LISTENER` |
| `input`、`used` | 保存已收到的请求数据及有效长度 |
| `output`、`output_len`、`sent` | 保存当前待发送的数据块、块长度和已发送长度 |
| `file_fd`、`file_offset`、`file_remaining` | 保存打开的文件、下一次读取位置及尚未读入缓冲区的字节数 |

这一划分使请求解析结果与连接运行状态各有归属：构造响应后不再需要保留 `http_request`，但文件和网络的传输进度仍保存在 `connection` 中，供下一次事件继续使用。

### 2.4 函数分工与调用关系

| 函数 | 主要职责 |
| --- | --- |
| `main()` | 初始化 TLS、建立双端口监听，运行 `epoll_wait()` 主循环并分派事件 |
| `make_listener()`、`nonblock()` | 创建监听套接字、设置地址复用与非阻塞模式，完成绑定和监听 |
| `accept_clients()` | 接收连接，分配 `connection`，按需创建 `SSL` 对象，注册事件并首次调用 `drive()` |
| `drive()` | 执行连接状态机，协调握手、请求读取、响应发送和关闭 |
| `watch()`、`tls_wait()` | 注册或修改关注的事件；将 TLS 的等待状态转换为读写事件 |
| `parse_request()`、`is_token()` | 切分并检查 HTTP 请求行、首部字段，提取请求信息 |
| `build_response()`、`empty_response()` | 根据请求决定状态码和响应首部，初始化文件发送范围；统一生成无正文响应 |
| `open_local_file()`、`hex_value()` | 解码 URL 路径并逐级打开本地文件 |
| `select_range()`、`parse_decimal()` | 解析字节范围和数值，计算文件起点、长度及边界结果 |
| `content_type()`、`read_file_chunk()` | 选择响应类型，按当前文件偏移读取下一块数据 |
| `drop()`、`die()` | 分别负责单连接资源清理和程序级致命错误退出 |

主要调用关系是 `main() → accept_clients()/drive() → build_response() → parse_request()/open_local_file()/select_range()`。其中，`build_response()` 负责确定“返回什么”，`drive()` 负责推进“何时读写、已经传输了多少”；响应构造完成后，`drive()` 再按需调用 `read_file_chunk()` 读取文件。

## 三、分阶段实现

### 3.0 AI 辅助说明

网络协议和事实标准实在复杂，包括 epoll 的使用也并不轻松。本次实验大量使用 AI 辅助纠错以及处理边界，实在节省不少纠错的功夫，程序鲁棒性也大幅增强。

### 3.1 建立双端口服务与 TLS 通道

服务器使用 `socket()` 创建 TCP 套接字，经 `bind()` 绑定端口后调用 `listen()` 进入监听状态。80 和 443 端口分别对应一个监听对象，并始终保留在事件循环中。通过 `SO_REUSEADDR` 支持服务重启后的地址复用，监听套接字和接收得到的客户端套接字均设置为非阻塞模式。

TLS 初始化使用 `TLS_server_method()` 创建服务端上下文，随后加载证书和私钥，并检查二者是否匹配。每个 HTTPS 连接创建独立的 `SSL` 对象，通过 `SSL_set_fd()` 关联底层 TCP 套接字。

非阻塞模式下，握手不一定能在一次调用中完成。程序根据 OpenSSL 返回的状态等待后续读写事件，握手成功后才进入 HTTP 请求读取阶段。这样，尚未完成握手的客户端不会使整个服务器停在 `SSL_accept()` 中。

### 3.2 用状态机与 epoll 管理连接

程序采用单线程事件循环，以 `epoll` 的水平触发模式管理监听套接字和客户端连接。连接状态分为五类：

| 状态 | 处理内容 | 后续状态或动作 |
| --- | --- | --- |
| `LISTENER` | 接收新连接 | 为新连接建立独立对象 |
| `HANDSHAKE` | 推进 TLS 握手 | 成功后进入 `READING` |
| `READING` | 累积请求头并解析 | 构造响应后进入 `WRITING` |
| `WRITING` | 发送响应头和文件数据 | 全部发送后进入 `CLOSING` |
| `CLOSING` | 发送 TLS 关闭通知并结束连接 | 释放文件、套接字和连接对象 |

事件循环通过 `epoll_event.data.ptr` 找到相应连接，再由 `drive()` 推进其状态。遇到暂时无法完成的 I/O 时，记录已有进度并返回事件循环。

#### （1）主事件循环：分派就绪连接

完成 TLS 初始化并把两个监听对象注册到 `epoll` 后，`main()` 执行以下主循环，原代码如下：

```c
struct epoll_event events[64];
while (1)
{
	int count = epoll_wait(ep, events, 64, -1);
	if (count < 0)
	{
		if (errno == EINTR)
			continue;
		die("epoll_wait");
	}
	for (int i = 0; i < count; ++i)
	{
		struct connection *c = events[i].data.ptr;
		if (c->state == LISTENER)
			accept_clients(ep, c, ctx);
		else if (drive(ep, c) < 0)
			drop(ep, c);
	}
}
```

`epoll_wait()` 在没有事件时等待，每次最多返回 64 个就绪对象。监听对象交给 `accept_clients()`，已建立的连接交给 `drive()`。`drive()` 返回 `0` 表示保留连接并等待后续事件，返回 `-1` 表示连接已结束或发生不可恢复的错误，两种结束情况均由 `drop()` 统一清理。

#### （2）连接状态机：推进一次请求

`drive()` 内部用 `for (;;)` 包围 `switch (c->state)`：握手成功后进入读取状态，请求头完整后调用 `build_response()` 并进入发送状态，响应全部发出后进入关闭状态。`break` 只退出 `switch`，因此可以立即推进下一状态；需要等待 I/O 时通过 `return` 交还控制权。以发送阶段的关键转移为例，原代码如下：

```c
left = c->output_len - c->sent;
if (left == 0)
{
	if (c->file_remaining == 0)
		c->state = CLOSING;
	else if (read_file_chunk(c) < 0)
		return -1;
	break;
}
```

当前缓冲区发完后，若文件还有剩余内容便读取下一块，否则进入 `CLOSING`；缓冲区仍有数据时则继续调用 `SSL_write()` 或 `send()`。这样，响应头、文件数据和连接关闭由同一个控制循环衔接。
新连接由 `calloc()` 分配并清零，HTTP 连接直接进入 `READING`，HTTPS 连接从 `HANDSHAKE` 开始。水平触发保证尚未处理完的就绪条件仍会继续通知。

#### （3）等待事件与资源回收

TLS 的“暂时不可读写”由以下原代码处理：

```c
int error = SSL_get_error(c->ssl, ret);
if (error == SSL_ERROR_WANT_READ)
	return watch(ep, EPOLL_CTL_MOD, c, EPOLLIN);
if (error == SSL_ERROR_WANT_WRITE)
	return watch(ep, EPOLL_CTL_MOD, c, EPOLLOUT);
return -1;
```

这里按 TLS 层实际需要的事件调整监听方向。普通 Socket 的 `EAGAIN`、`EWOULDBLOCK` 也采用类似处理；遇到 `EINTR` 则重试。

此外，每次处理监听事件最多接收 64 个连接，每次推进连接累计发送达到 64 KiB 后让出执行机会，以避免连接接收或大文件发送长期占用事件循环。这些机制用于支持连接间交替推进。

连接结束时统一调用 `drop()`：先从 `epoll` 中移除套接字，再释放 `SSL` 对象、关闭文件和连接描述符，最后释放连接结构体。文件描述符初始化为 `-1`，用于区分尚未打开文件的连接，避免清理时误关闭其他描述符。

### 3.3 解析 HTTP 请求并返回文件

#### （1）请求头的完整接收与解析

TCP 提供字节流，一次读取不一定得到完整 HTTP 请求。程序使用 4096 字节输入缓冲区累积数据，以 `\r\n\r\n` 判断请求头是否接收完整，并为字符串结束符保留空间。确认头部完整后，再解析方法、目标路径、HTTP 版本及各个首部字段。

解析过程检查请求行格式和首部字段名，要求 HTTP/1.1 请求带有唯一且非空的 `Host`，并以不区分大小写的方式识别 `Range`、`If-Range` 等字段。格式错误返回 `400 Bad Request`；缓冲区耗尽而请求头仍未完整时返回 `431 Request Header Fields Too Large`。

`parse_request()` 直接在输入缓冲区内切分字符串：把请求行中的空格、首部行末的换行符和字段名后的冒号替换为字符串结束符，再用指针引用方法、路径和字段值。首部值两端的空格和制表符会被去除。这种处理无需为每个字段单独分配内存，而输入缓冲区在响应构造完成前始终有效。

#### （2）HTTP 重定向

通过格式检查的 HTTP 请求进入重定向分支，保留原路径和查询参数，以固定的 `https://10.0.0.1` 为目标地址。该分支不读取文件，因此不存在的文件路径也先重定向，再由 HTTPS 服务判断是否返回 404。

重定向地址写入 `Location` 首部，再由 `empty_response()` 统一生成状态行、`Content-Length: 0` 和 `Connection: close`。404 等无正文响应也复用该函数；正常文件响应则根据文件大小或选定范围单独填写正文长度。

#### （3）HTTPS 文件响应与路径处理

HTTPS 文件服务支持 `GET`，其他方法返回 `405 Method Not Allowed`，并使用 `Allow: GET` 告知支持的方法。

路径解析只取 URL 中查询参数之前的部分，并进行百分号解码。根路径 `/` 映射为 `index.html`，子目录文件则按路径逐级访问。程序拒绝 `..` 路径分量，使用 `openat()` 和 `O_NOFOLLOW` 逐级打开路径，避免通过父目录跳转或符号链接访问工作目录之外的文件；最终通过 `fstat()` 确认目标为普通文件。目标不存在或不满足文件访问条件时，返回无正文的 404 响应。这部分完全是透过 AI 修改的代码才知道要防御 TOCTOU 。

正常文件响应设置 `Content-Length`、`Content-Type`、`Accept-Ranges` 和 `Connection: close`。服务器每条连接处理一次请求，发送完成后关闭连接。

文件内容不一次性载入内存，而是复用 8192 字节输出缓冲区，通过 `pread()` 从 `file_offset` 指定的位置分块读取。每次成功读取后增加文件偏移、减少 `file_remaining`，并设置当前块的 `output_len`、将 `sent` 归零。只有当前块完全发送后才读取下一块，从而分别保存文件读取进度和网络发送进度。

发送时，以 `output_len - sent` 计算当前剩余长度，从 `output + sent` 继续写出，并仅按实际成功写入的字节数增加 `sent`。如果需要等待可写事件，缓冲区内容和发送位置保持不变，下一次从同一位置继续。响应头也先放入该缓冲区，完整发出后才装入第一块文件数据；只有缓冲区已发完且 `file_remaining` 为零时，才进入关闭状态。

### 3.4 第四阶段：实现 Range 分段传输

服务器在获取文件大小后，由 `select_range()` 计算实际发送的起点和长度。没有范围请求时返回完整文件；有效单段范围返回 `206 Partial Content`，并增加 `Content-Range` 首部。

| 请求形式 | 本实现的含义 |
| --- | --- |
| `bytes=a-b` | 返回从偏移 `a` 到 `b` 的字节，包含两个端点 |
| `bytes=a-` | 从偏移 `a` 返回至文件末尾 |
| `bytes=-n` | 返回文件最后 `n` 个字节 |

对于起止位置已经解析完成的范围，边界处理原代码如下：

```c
if (end < start)
	return 0;
if (start >= size)
	return -1;
if (end >= size)
	end = size - 1;
*first = start;
*length = end - start + 1;
return 1;
```

函数返回 `0` 表示忽略该范围并发送完整文件，返回 `-1` 表示范围无法满足，返回 `1` 表示生成部分内容响应。结束位置超过文件末尾时，截断到最后一个字节；起点已超出文件时，返回 `416 Range Not Satisfiable` 和 `Content-Range: bytes */文件大小`。

以本实验 48,957 字节的文件为例：

| Range 请求 | Content-Range | 正文长度 |
| --- | --- | ---: |
| `bytes=100-200` | `bytes 100-200/48957` | 101 字节 |
| `bytes=100-` | `bytes 100-48956/48957` | 48,857 字节 |

计算得到的起点和长度分别写入 `file_offset` 与 `file_remaining`，使完整文件和部分文件共用同一套输出逻辑。每次读取的字节数取缓冲区容量与剩余长度的较小值，因此范围末尾即使位于文件中间，也不会继续发送该范围之外的内容。

本实现对多个范围、重复的 `Range` 首部以及带有未验证 `If-Range` 条件的请求回退为完整的 200 响应，不生成多段 `multipart/byteranges` 响应。范围数值解析使用溢出饱和处理，避免过大的十进制数发生整数回绕。

## 四、实验结果与分析

线上验证已通过。

## 五、实验总结

本实验完成了同时提供 HTTP 重定向和 HTTPS 文件访问的 Socket 服务器，并通过线上验证。实现以非阻塞连接状态机为基础，将 TCP 连接、TLS 握手、HTTP 解析和文件发送组织在同一个 `epoll` 事件循环中；通过独立记录每条连接的读写进度，支持请求分段到达和文件分块发送；实现采用每连接一次请求的关闭策略，HTTPS 文件服务仅支持 GET 和单段范围传输。

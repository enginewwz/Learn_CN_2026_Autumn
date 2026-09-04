# Mininet 网站延迟与 c-latency 测量

本目录的脚本从 Mininet 主机命名空间内访问约 1000 个网站，输出 CSV，并画出
Ping、DNS、TCP transfer 和 Total time 与地理传播下界 `c-latency` 的关系图。

## 指标口径

口径参考论文 [The Internet at the Speed of Light](https://people.eecs.berkeley.edu/~sylvia/cs268-2019/papers/speed.pdf)：

- **Ping**：对网站实际连接 IP 发 3 个 ICMP echo，记录最小 RTT。
- **DNS**：libcurl 的 `NAMELOOKUP_TIME`。
- **TCP transfer**：接收到 HTTP 响应首字节至末字节的耗时，计算为
  `TOTAL_TIME - STARTTRANSFER_TIME`。CSV 字段名为 `tcp_transfer_ms`。
- **Total time**：从请求开始到 HTTP 响应体接收结束的总时间。
- **c-latency**：测量点到 GeoIP 坐标的大圆距离，按真空光速
  `299792.458 km/s` 计算理论最小 RTT：`2 * distance / c`。如需研究光纤
  下界，可传入 `--propagation-speed 200000`。

GeoIP 位置通常是近似位置；CDN/Anycast IP 的注册位置也不一定等于实际服务节点，
因此 c-latency 只适合当作粗略下界。

## 1. Conda 环境

当前实验环境名为 `CN_lab_env`：

```bash
conda activate CN_lab_env
python -c "import pycurl, geoip2, matplotlib; print('dependencies OK')"
```

如果需要在另一台机器上重建环境，可执行：

```bash
cd Sept3
conda create -n CN_lab_env python=3.14
conda activate CN_lab_env
python -m pip install -r requirements.txt
```

若 Ubuntu/Debian 上安装 `pycurl` 时提示找不到 libcurl 头文件，先安装系统依赖：

```bash
sudo apt-get install libcurl4-openssl-dev python3-dev build-essential
```

系统还需有 `ping` 命令。请按 [MaxMind GeoLite 官方说明](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/)
下载 **GeoLite2 City** 数据库并解压，得到 `GeoLite2-City.mmdb`；数据库文件不要
提交到仓库。

## 2. 获取 1000 个站点

```bash
python download_sites.py --count 1000 --output sites.csv
```

脚本默认下载当前 [Tranco Top Sites](https://tranco-list.eu/) 列表。也可以自行准备
`rank,domain` CSV，或每行一个域名的文本文件。

## 3. 在 Mininet 内运行

先确定真实测量出口的大致经纬度。

普通终端中可先激活环境再运行：

```bash
conda activate CN_lab_env
cd /home/enginew/CN_lab_2026_Autumn/Sept3
MPLCONFIGDIR=/tmp/matplotlib-cn-lab python measure_websites.py \
  --sites sites.csv \
  --geoip-db GeoLite2-City.mmdb \
  --source-lat 35.6764 --source-lon 139.6500 \
  --output results.csv --plot latency_inflation_cdf.png
```

Mininet CLI 通常不是完整的交互式 Bash，不能稳定使用 `conda activate`。直接调用
`CN_lab_env` 中 Python 的绝对路径：

(bj↓)

```text
mininet> h1 cd /home/enginew/CN_lab_2026_Autumn/Sept3 && MPLCONFIGDIR=/tmp/matplotlib-cn-lab /home/enginew/anaconda3/envs/CN_lab_env/bin/python measure_websites.py --sites sites.csv --geoip-db GeoLite2-City.mmdb --source-lat 39.9075 --source-lon 116.3972 --output results.csv --plot latency_inflation_cdf.png
```

也可以在自定义拓扑 Python 代码中使用：

```python
h1.cmd(
    "cd /home/enginew/CN_lab_2026_Autumn/Sept3 && "
    "MPLCONFIGDIR=/tmp/matplotlib-cn-lab "
    "/home/enginew/anaconda3/envs/CN_lab_env/bin/python measure_websites.py "
    "--sites sites.csv --geoip-db GeoLite2-City.mmdb "
    "--source-lat 35.6895 --source-lon 139.6917 "
    "> measurement.log 2>&1 &"
)
```

默认 20 个并发任务、每站 HTTP 总超时 12 秒。若实验链路较慢，可以使用
`--workers 8 --timeout 20`。每完成一个站点都会刷新 CSV；中断后用相同命令重跑
会自动跳过已有域名。需要全新实验时加 `--no-resume`。

## 4. 单独重画图

```bash
python plot_results.py results.csv --output latency_inflation_cdf.png
```

输出口径与参考论文一致：横轴是 `实测指标 / c-latency`，表示相对理论下界的
延迟膨胀倍数，使用 `1–1000x` 对数坐标；纵轴是经验累积分布 CDF，范围为 0–1。
Ping、DNS、TCP transfer 和 Total time 各占一条曲线。图例同时显示各指标的
中位膨胀倍数和有效样本数。小于 1x 或大于 1000x 的值只在显示时裁剪到坐标轴
边界，计算中不会删除。

## 5. 清洗结果

严格清洗会保留公网 IP、HTTP 2xx/3xx、五项数值完整且为正、阶段时间一致，并且
`Ping >= c-latency` 的记录。原始文件不会被覆盖，所有剔除记录及原因会写入另一
个 CSV：

```bash
python clean_results.py results.csv \
  --output results_clean.csv \
  --rejected results_rejected.csv
```

如需额外剔除任一指标超过各自 P99 的统计异常值：

```bash
python clean_results.py results.csv \
  --trim-percentile 99 \
  --output results_clean_p99.csv \
  --rejected results_rejected_p99.csv
python plot_results.py results_clean_p99.csv \
  --output latency_inflation_cdf_p99.png
```

`--keep-geo-anomalies` 可以保留 `Ping < c-latency` 的记录，但不建议用于最终的
c-latency 对比图。

若关系图在某个 c-latency 位置堆积，通常是 GeoLite 给大量 IP 返回了国家中心点。
最终 CDF 建议同时排除定位精度半径超过 200 km 的记录。不要对最终 CDF 做 P99
裁剪，因为分布长尾本身就是研究对象：

```bash
python clean_results.py results.csv \
  --geoip-db GeoLite2-City.mmdb \
  --max-accuracy-radius-km 200 \
  --require-distance-beyond-accuracy \
  --output results_clean_geo200.csv \
  --rejected results_rejected_geo200.csv
python plot_results.py results_clean_geo200.csv \
  --output latency_inflation_cdf_clean.png \
  --x-min 1 --x-max 1000
```

`--require-distance-beyond-accuracy` 还会剔除源站距离小于 GeoIP 定位误差半径的
记录，避免距离接近零时 `measured / c-latency` 被不稳定的小分母放大。

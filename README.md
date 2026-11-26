# MCU Telnet Logger

`mcu_telnet_logger` 是一个用于持续监听 MCU Telnet Shell 输出并采集 BMS
数据的 Python 工具。默认每 10 秒发送一次 `bms sample`。

它会把收到的日志同时：

- 打印到当前终端
- 将包含收发方向和主机时间戳的原始数据保存到 TXT 文件
- 将完整的 `bms sample` 响应转换成一行精简 CSV
- 支持在终端输入 Zephyr Shell 命令并转发给 MCU
- 断线后自动重连

该工具只依赖 Python 标准库，不需要安装额外依赖。

## 目录结构

```text
mcu_telnet_logger/
├── mcu_telnet_logger.py
├── telnet_log_config.json
├── logs/
└── README.md
```

`logs/` 目录用于保存运行日志，默认不会提交到 git。

## 配置

编辑 `telnet_log_config.json`：

```json
{
  "host": "192.168.46.36",
  "port": 23,
  "raw_output_file": "logs/bms_raw.txt",
  "csv_output_file": "logs/bms_samples.csv",
  "append": false,
  "connect_timeout_seconds": 10,
  "read_timeout_seconds": 1,
  "reconnect_interval_seconds": 5,
  "encoding": "utf-8",
  "strip_ansi": true,
  "send_initial_newline": true,
  "interactive": true,
  "bms_sample_interval_seconds": 10,
  "bms_response_timeout_seconds": 2
}
```

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `host` | MCU 的 IPv4 地址或主机名 |
| `port` | Telnet 端口，Zephyr Shell 默认通常是 `23` |
| `raw_output_file` | 原始 Telnet 收发记录路径，相对于配置文件所在目录 |
| `csv_output_file` | BMS 结构化 CSV 路径，相对于配置文件所在目录 |
| `append` | `true` 表示追加写入，`false` 表示每次启动覆盖 |
| `reconnect_interval_seconds` | 断线后的重连间隔 |
| `strip_ansi` | 是否移除终端颜色控制字符 |
| `interactive` | 是否允许在终端输入命令并发送到 MCU |
| `bms_sample_interval_seconds` | 自动发送 `bms sample` 的周期，现场默认 10 秒 |
| `bms_response_timeout_seconds` | 等待一组完整 BMS 响应的超时时间 |

## 运行

在工具目录中运行：

```powershell
python .\mcu_telnet_logger.py
```

也可以在其他目录指定配置文件：

```powershell
python C:\path\to\mcu_telnet_logger.py -c C:\path\to\telnet_log_config.json
```

连接成功后，MCU 输出会实时显示在终端，并写入：

```text
logs/bms_raw.txt
logs/bms_samples.csv
```

原始文件保留 MCU 返回的所有内容，并使用 `[TX]`、`[RX]` 标记方向。CSV
使用以下精简列：

```csv
seq,time,online,valid,soc,voltage_v,current_a,direction,capacity_mah,temp_c,status,raw_status,ok
1,2026-11-26T14:30:00.123+08:00,1,0x0000003f,85.0,51.620,1.240,charge,28000,25.300,1,0x00000001,1
```

`ok=1` 表示十个 `bms sample` 字段全部收到；超时或响应不完整时为
`ok=0`，缺失值留空。YT46 的电流是幅值，充放电方向根据 `status` 转换为
`idle`、`charge`、`discharge` 或 `unknown`。

## 交互命令

当 `interactive` 为 `true` 时，可以直接在当前终端输入 Zephyr Shell 命令：

```text
fw_time
log status
ota show
```

按 Enter 后命令会发送给 MCU。

## 停止

按 `Ctrl+C` 停止脚本。

## 注意事项

Zephyr Telnet Shell 通常更适合作为单客户端交互入口。如果该脚本保持连接，PuTTY、Windows telnet 等其他客户端可能无法同时稳定连接同一个 Telnet Shell。

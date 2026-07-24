# MCU Telnet Logger

`mcu_telnet_logger` 用来持续监听 MCU 的 Telnet Shell 输出，并把日志打印到终端、保存到本地文件。

## 快速开始

1. 修改 [telnet_log_config.json](telnet_log_config.json) 里的 `host` 和需要的参数。
2. 在脚本目录执行 `python3 mcu_telnet_logger.py`。
3. 连接成功后，日志会实时显示在终端，并写入 `logs/mcu_telnet_log.txt`。
4. 需要交互输入时，直接在终端输入 Zephyr Shell 命令并回车。

脚本会在连接成功后主动探活一次，之后每隔半小时继续探活；断线后会自动重连。

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
  "host": "192.168.101.32",
  "port": 23,
  "output_file": "logs/mcu_telnet_log.txt",
  "append": true,
  "connect_timeout_seconds": 10,
  "read_timeout_seconds": 1,
  "reconnect_interval_seconds": 5,
  "encoding": "utf-8",
  "strip_ansi": true,
  "line_timestamp": false,
  "send_initial_newline": true,
  "interactive": true,
  "probe_interval_seconds": 1800,
  "probe_response_timeout_seconds": 10,
  "probe_command": "date get"
}
```

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `host` | MCU 的 IPv4 地址或主机名 |
| `port` | Telnet 端口，Zephyr Shell 默认通常是 `23` |
| `output_file` | 日志 txt 保存路径，相对于配置文件所在目录 |
| `append` | `true` 表示追加写入，`false` 表示每次启动覆盖 |
| `reconnect_interval_seconds` | 断线后的重连间隔 |
| `strip_ansi` | 是否移除终端颜色控制字符 |
| `line_timestamp` | 是否给保存到文件的每行日志加 PC 本地时间戳 |
| `interactive` | 是否允许在终端输入命令并发送到 MCU |
| `probe_interval_seconds` | 主动探活间隔，默认 1800 秒，也就是 30 分钟 |
| `probe_response_timeout_seconds` | 探活命令发出后等待时间响应的超时时间 |
| `probe_command` | 主动探活时发送的命令，默认 `date get` |

主动探活的第一次会发生在首次连接成功后，这一次会被视为 `00:00`；之后脚本每隔半小时发送一次 `date get`。如果在 `probe_response_timeout_seconds` 内没有等到时间响应，脚本会认为连接失效并按固定间隔重连。

## 运行

在工具目录中运行：

```powershell
python3 mcu_telnet_logger.py
```

也可以在其他目录指定配置文件：

```powershell
python3 /path/to/mcu_telnet_logger.py -c /path/to/telnet_log_config.json
```

连接成功后，MCU 输出会实时显示在终端，并写入：

```text
logs/mcu_telnet_log.txt
```

## 交互命令

当 `interactive` 为 `true` 时，可以直接在当前终端输入 Zephyr Shell 命令：

```text
fw_time
log status
ota show
```

按 Enter 后命令会发送给 MCU。

主动探活使用的是 `date get`，期望 MCU 返回类似 `2026-07-24 01:28:59 UTC` 这样的时间字符串。

## 停止

按 `Ctrl+C` 停止脚本。

## 注意事项

Zephyr Telnet Shell 通常更适合作为单客户端交互入口。如果该脚本保持连接，PuTTY、Windows telnet 等其他客户端可能无法同时稳定连接同一个 Telnet Shell。

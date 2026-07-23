# MCU Telnet Logger

`mcu_telnet_logger` 是一个用于持续监听 MCU Telnet Shell 输出的 Python 工具。

它会把收到的日志同时：

- 打印到当前终端
- 保存到本地 txt 文件
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
  "interactive": true
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

## 停止

按 `Ctrl+C` 停止脚本。

## 注意事项

Zephyr Telnet Shell 通常更适合作为单客户端交互入口。如果该脚本保持连接，PuTTY、Windows telnet 等其他客户端可能无法同时稳定连接同一个 Telnet Shell。

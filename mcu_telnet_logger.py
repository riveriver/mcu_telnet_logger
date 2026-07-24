#!/usr/bin/env python3
"""Continuously capture MCU Telnet shell logs into a text file."""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

ANSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class MonitorConfig:
    host: str
    port: int
    output_file: Path
    append: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    reconnect_interval_seconds: float
    encoding: str
    strip_ansi: bool
    line_timestamp: bool
    send_initial_newline: bool
    interactive: bool


class TelnetFilter:
    """Remove Telnet negotiation bytes and reply with conservative refusals."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.state = "data"
        self.command = 0

    def feed(self, data: bytes) -> bytes:
        output = bytearray()

        for byte in data:
            if self.state == "data":
                if byte == IAC:
                    self.state = "iac"
                else:
                    output.append(byte)
                continue

            if self.state == "iac":
                if byte == IAC:
                    output.append(IAC)
                    self.state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self.command = byte
                    self.state = "option"
                elif byte == SB:
                    self.state = "subnegotiation"
                else:
                    self.state = "data"
                continue

            if self.state == "option":
                self._refuse_option(self.command, byte)
                self.state = "data"
                continue

            if self.state == "subnegotiation" and byte == IAC:
                self.state = "subnegotiation_iac"
                continue

            if self.state == "subnegotiation_iac":
                self.state = "data" if byte == SE else "subnegotiation"

        return bytes(output)

    def _refuse_option(self, command: int, option: int) -> None:
        if command == DO:
            response = bytes((IAC, WONT, option))
        elif command == WILL:
            response = bytes((IAC, DONT, option))
        else:
            return

        try:
            self.sock.sendall(response)
        except OSError:
            pass

class LineTimestampWriter:
    def __init__(self, file_obj: Any, enabled: bool) -> None:
        self.file_obj = file_obj
        self.enabled = enabled
        self.at_line_start = True

    def write(self, text: str) -> None:
        if not self.enabled:
            self.file_obj.write(text)
            self.file_obj.flush()
            return

        for char in text:
            if self.at_line_start and char not in "\r\n":
                stamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")
                self.file_obj.write(stamp)
                self.at_line_start = False

            self.file_obj.write(char)

            if char == "\n":
                self.at_line_start = True

        self.file_obj.flush()


class ActiveConnection:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self.connection_lost = threading.Event()
        self.stop_requested = threading.Event()

    def set(self, sock: socket.socket) -> None:
        with self._lock:
            self._sock = sock
            self._connected.set()
            self.connection_lost.clear()

    def clear(self, sock: socket.socket) -> None:
        with self._lock:
            if self._sock is sock:
                self._sock = None
                self._connected.clear()

    def mark_lost(self) -> None:
        self.connection_lost.set()

    def send_line(self, line: str, encoding: str) -> bool:
        if not self._connected.wait(timeout=0.1):
            return False

        if line.endswith("\n"):
            line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]

        payload = f"{line}\r\n".encode(encoding, errors="replace")

        with self._lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(payload)
                return True
            except OSError:
                self._connected.clear()
                self.connection_lost.set()
                return False


def configure_socket_keepalive(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return

    keepalive_options = (
        (getattr(socket, "TCP_KEEPIDLE", None), 10),
        (getattr(socket, "TCP_KEEPINTVL", None), 3),
        (getattr(socket, "TCP_KEEPCNT", None), 3),
    )

    for option, value in keepalive_options:
        if option is None:
            continue

        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            continue


def load_config(path: Path) -> MonitorConfig:
    with path.open("r", encoding="utf-8") as file_obj:
        raw = json.load(file_obj)

    output_file = Path(raw.get("output_file", "logs/mcu_telnet_log.txt"))
    if not output_file.is_absolute():
        output_file = (path.parent / output_file).resolve()

    return MonitorConfig(
        host=str(raw["host"]),
        port=int(raw.get("port", 23)),
        output_file=output_file,
        append=bool(raw.get("append", True)),
        connect_timeout_seconds=float(raw.get("connect_timeout_seconds", 10)),
        read_timeout_seconds=float(raw.get("read_timeout_seconds", 1)),
        reconnect_interval_seconds=float(raw.get("reconnect_interval_seconds", 5)),
        encoding=str(raw.get("encoding", "utf-8")),
        strip_ansi=bool(raw.get("strip_ansi", True)),
        line_timestamp=bool(raw.get("line_timestamp", False)),
        send_initial_newline=bool(raw.get("send_initial_newline", True)),
        interactive=bool(raw.get("interactive", True)),
    )


def decode_log_bytes(data: bytes, config: MonitorConfig) -> str:
    if config.strip_ansi:
        data = ANSI_RE.sub(b"", data)

    return data.decode(config.encoding, errors="replace")


def stdin_sender(config: MonitorConfig, active: ActiveConnection) -> None:
    while not active.stop_requested.is_set():
        line = sys.stdin.readline()
        if line == "":
            active.stop_requested.set()
            return

        if not active.send_line(line, config.encoding):
            print("Not connected. Command was not sent.")


def monitor_once(config: MonitorConfig, writer: LineTimestampWriter, active: ActiveConnection) -> None:
    address = (config.host, config.port)
    with socket.create_connection(address, timeout=config.connect_timeout_seconds) as sock:
        configure_socket_keepalive(sock)
        sock.settimeout(config.read_timeout_seconds)
        active.set(sock)
        telnet_filter = TelnetFilter(sock)
        print(f"Connected to {config.host}:{config.port}")
        if config.interactive:
            print("Type shell commands here and press Enter to send them to the MCU.")

        if config.send_initial_newline:
            try:
                sock.sendall(b"\r\n")
            except OSError as error:
                active.mark_lost()
                raise ConnectionError("failed to send initial newline") from error

        try:
            while not active.stop_requested.is_set():
                if active.connection_lost.is_set():
                    raise ConnectionError("connection lost while sending data")

                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue

                if not chunk:
                    raise ConnectionError("remote closed the connection")

                payload = telnet_filter.feed(chunk)
                if payload:
                    text = decode_log_bytes(payload, config)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    writer.write(text)
        finally:
            active.clear(sock)


def run_forever(config: MonitorConfig) -> None:
    config.output_file.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if config.append else "w"

    print(f"Saving Telnet log to {config.output_file}")
    active = ActiveConnection()
    if config.interactive:
        thread = threading.Thread(target=stdin_sender, args=(config, active), daemon=True)
        thread.start()

    with config.output_file.open(mode, encoding=config.encoding, errors="replace", buffering=1) as file_obj:
        writer = LineTimestampWriter(file_obj, config.line_timestamp)
        while not active.stop_requested.is_set():
            try:
                monitor_once(config, writer, active)
            except KeyboardInterrupt:
                active.stop_requested.set()
                print("\nStopped.")
                return
            except OSError as error:
                print(f"Connection error: {error}")
            except ConnectionError as error:
                print(f"Disconnected: {error}")

            if active.stop_requested.is_set():
                break

            print(f"Reconnecting in {config.reconnect_interval_seconds:g} seconds...")
            time.sleep(config.reconnect_interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Zephyr Telnet shell logs and append them to a text file."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path(__file__).with_name("telnet_log_config.json"),
        help="Path to the JSON config file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config.resolve())
    run_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

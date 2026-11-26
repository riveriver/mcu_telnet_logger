#!/usr/bin/env python3
"""Continuously capture MCU Telnet shell logs into a text file."""

from __future__ import annotations

import argparse
import csv
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
    raw_output_file: Path
    csv_output_file: Path
    append: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    reconnect_interval_seconds: float
    encoding: str
    strip_ansi: bool
    send_initial_newline: bool
    interactive: bool
    bms_sample_interval_seconds: float
    bms_response_timeout_seconds: float


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


def timestamp_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class RawLogWriter:
    """Write timestamped TX/RX records while preserving partial RX lines."""

    def __init__(self, file_obj: Any) -> None:
        self.file_obj = file_obj
        self.at_line_start = True
        self._lock = threading.Lock()

    def write_received(self, text: str) -> None:
        with self._lock:
            for char in text:
                if self.at_line_start and char not in "\r\n":
                    self.file_obj.write(f"[{timestamp_iso()}] [RX] ")
                    self.at_line_start = False

                self.file_obj.write(char)
                if char == "\n":
                    self.at_line_start = True
            self.file_obj.flush()

    def write_sent(self, command: str) -> None:
        with self._lock:
            if not self.at_line_start:
                self.file_obj.write("\n")
            self.file_obj.write(f"[{timestamp_iso()}] [TX] {command}\n")
            self.file_obj.flush()
            self.at_line_start = True


CSV_FIELDS = (
    "seq", "time", "online", "valid", "soc", "voltage_v", "current_a",
    "direction", "capacity_mah", "temp_c", "status", "raw_status", "ok",
)

BMS_FIELDS = (
    "online", "valid_fields", "soc_raw", "soc_reg", "voltage_mv",
    "current_ma", "remaining_capacity_mah", "temperature_mc",
    "charge_status", "raw_status",
)


class BmsCsvCollector:
    """Collect one multi-line ``bms sample`` response into one CSV row."""

    def __init__(self, file_obj: Any, response_timeout_seconds: float) -> None:
        self.writer = csv.DictWriter(file_obj, fieldnames=CSV_FIELDS)
        if file_obj.tell() == 0:
            self.writer.writeheader()
            file_obj.flush()
        self.file_obj = file_obj
        self.response_timeout_seconds = response_timeout_seconds
        self.sequence = 0
        self.pending: dict[str, str] | None = None
        self.pending_time = ""
        self.deadline = 0.0
        self.line_buffer = ""
        self._lock = threading.Lock()

    def begin_sample(self) -> None:
        with self._lock:
            self._finish_locked(False)
            self.sequence += 1
            self.pending = {}
            self.pending_time = timestamp_iso()
            self.deadline = time.monotonic() + self.response_timeout_seconds

    def cancel_sample(self) -> None:
        with self._lock:
            self.pending = None

    def expire(self) -> None:
        with self._lock:
            self._expire_locked()

    def feed(self, text: str) -> None:
        with self._lock:
            self.line_buffer += text.replace("\r", "")
            while "\n" in self.line_buffer:
                line, self.line_buffer = self.line_buffer.split("\n", 1)
                self._consume_line_locked(line)
            self._expire_locked()

    def expire(self) -> None:
        with self._lock:
            self._expire_locked()

    def close(self) -> None:
        with self._lock:
            if self.line_buffer:
                self._consume_line_locked(self.line_buffer)
                self.line_buffer = ""
            self._finish_locked(False)

    def _consume_line_locked(self, line: str) -> None:
        if self.pending is None:
            return
        for key in BMS_FIELDS:
            match = re.search(rf"(?:^|\s){re.escape(key)}:\s*(\S+)", line)
            if match:
                self.pending[key] = match.group(1)
                break
        if all(key in self.pending for key in BMS_FIELDS):
            self._finish_locked(True)

    def _expire_locked(self) -> None:
        if self.pending is not None and time.monotonic() >= self.deadline:
            self._finish_locked(False)

    @staticmethod
    def _integer(fields: dict[str, str], key: str) -> int | None:
        try:
            return int(fields[key], 0)
        except (KeyError, ValueError):
            return None

    def _finish_locked(self, complete: bool) -> None:
        if self.pending is None:
            return

        fields = self.pending
        online = fields.get("online")
        soc_raw = self._integer(fields, "soc_raw")
        voltage_mv = self._integer(fields, "voltage_mv")
        current_ma = self._integer(fields, "current_ma")
        capacity_mah = self._integer(fields, "remaining_capacity_mah")
        temperature_mc = self._integer(fields, "temperature_mc")
        status = self._integer(fields, "charge_status")
        direction = {0: "idle", 1: "charge", 2: "discharge"}.get(status, "unknown")

        row = {
            "seq": self.sequence,
            "time": self.pending_time,
            "online": {"true": 1, "false": 0}.get(str(online).lower(), ""),
            "valid": fields.get("valid_fields", ""),
            "soc": "" if soc_raw is None else f"{soc_raw / 10:.1f}",
            "voltage_v": "" if voltage_mv is None else f"{voltage_mv / 1000:.3f}",
            "current_a": "" if current_ma is None else f"{current_ma / 1000:.3f}",
            "direction": direction,
            "capacity_mah": "" if capacity_mah is None else capacity_mah,
            "temp_c": "" if temperature_mc is None else f"{temperature_mc / 1000:.3f}",
            "status": "" if status is None else status,
            "raw_status": fields.get("raw_status", ""),
            "ok": 1 if complete else 0,
        }
        self.writer.writerow(row)
        self.file_obj.flush()
        self.pending = None


class ActiveConnection:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self.stop_requested = threading.Event()

    def set(self, sock: socket.socket) -> None:
        with self._lock:
            self._sock = sock
            self._connected.set()

    def clear(self, sock: socket.socket) -> None:
        with self._lock:
            if self._sock is sock:
                self._sock = None
                self._connected.clear()

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
                return False

    def wait_connected(self, timeout: float) -> bool:
        return self._connected.wait(timeout=timeout)


def resolve_output_path(path: Path, value: str) -> Path:
    output_path = Path(value)
    if not output_path.is_absolute():
        output_path = (path.parent / output_path).resolve()
    return output_path


def load_config(path: Path) -> MonitorConfig:
    with path.open("r", encoding="utf-8") as file_obj:
        raw = json.load(file_obj)

    raw_output = raw.get("raw_output_file", raw.get("output_file", "logs/bms_raw.txt"))

    return MonitorConfig(
        host=str(raw["host"]),
        port=int(raw.get("port", 23)),
        raw_output_file=resolve_output_path(path, str(raw_output)),
        csv_output_file=resolve_output_path(
            path, str(raw.get("csv_output_file", "logs/bms_samples.csv"))
        ),
        append=bool(raw.get("append", True)),
        connect_timeout_seconds=float(raw.get("connect_timeout_seconds", 10)),
        read_timeout_seconds=float(raw.get("read_timeout_seconds", 1)),
        reconnect_interval_seconds=float(raw.get("reconnect_interval_seconds", 5)),
        encoding=str(raw.get("encoding", "utf-8")),
        strip_ansi=bool(raw.get("strip_ansi", True)),
        send_initial_newline=bool(raw.get("send_initial_newline", True)),
        interactive=bool(raw.get("interactive", True)),
        bms_sample_interval_seconds=float(raw.get("bms_sample_interval_seconds", 10)),
        bms_response_timeout_seconds=float(raw.get("bms_response_timeout_seconds", 2)),
    )


def decode_log_bytes(data: bytes, config: MonitorConfig) -> str:
    if config.strip_ansi:
        data = ANSI_RE.sub(b"", data)

    return data.decode(config.encoding, errors="replace")


def stdin_sender(
    config: MonitorConfig, active: ActiveConnection, raw_writer: RawLogWriter
) -> None:
    while not active.stop_requested.is_set():
        line = sys.stdin.readline()
        if line == "":
            active.stop_requested.set()
            return

        command = line.rstrip("\r\n")
        if active.send_line(command, config.encoding):
            raw_writer.write_sent(command)
        else:
            print("Not connected. Command was not sent.")


def periodic_bms_sender(
    config: MonitorConfig,
    active: ActiveConnection,
    raw_writer: RawLogWriter,
    collector: BmsCsvCollector,
) -> None:
    interval = config.bms_sample_interval_seconds
    next_run = time.monotonic()

    while not active.stop_requested.is_set():
        if not active.wait_connected(timeout=0.5):
            next_run = time.monotonic()
            continue

        now = time.monotonic()
        if now < next_run:
            collector.expire()
            active.stop_requested.wait(min(next_run - now, 0.5))
            continue

        collector.begin_sample()
        raw_writer.write_sent("bms sample")
        if not active.send_line("bms sample", config.encoding):
            collector.cancel_sample()

        next_run += interval
        while next_run <= time.monotonic():
            next_run += interval


def monitor_once(
    config: MonitorConfig,
    raw_writer: RawLogWriter,
    collector: BmsCsvCollector,
    active: ActiveConnection,
) -> None:
    address = (config.host, config.port)
    with socket.create_connection(address, timeout=config.connect_timeout_seconds) as sock:
        sock.settimeout(config.read_timeout_seconds)
        active.set(sock)
        telnet_filter = TelnetFilter(sock)
        print(f"Connected to {config.host}:{config.port}")
        if config.interactive:
            print("Type shell commands here and press Enter to send them to the MCU.")

        if config.send_initial_newline:
            sock.sendall(b"\r\n")

        try:
            while not active.stop_requested.is_set():
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
                    raw_writer.write_received(text)
                    collector.feed(text)
        finally:
            active.clear(sock)


def run_forever(config: MonitorConfig) -> None:
    if config.bms_sample_interval_seconds <= 0:
        raise ValueError("bms_sample_interval_seconds must be greater than zero")
    if config.bms_response_timeout_seconds <= 0:
        raise ValueError("bms_response_timeout_seconds must be greater than zero")

    config.raw_output_file.parent.mkdir(parents=True, exist_ok=True)
    config.csv_output_file.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if config.append else "w"

    print(f"Saving raw Telnet log to {config.raw_output_file}")
    print(f"Saving BMS samples to {config.csv_output_file}")
    print(f"Sending 'bms sample' every {config.bms_sample_interval_seconds:g} seconds")
    active = ActiveConnection()
    with (
        config.raw_output_file.open(
            mode, encoding=config.encoding, errors="replace", buffering=1
        ) as raw_file,
        config.csv_output_file.open(
            mode, encoding="utf-8", newline="", buffering=1
        ) as csv_file,
    ):
        raw_writer = RawLogWriter(raw_file)
        collector = BmsCsvCollector(csv_file, config.bms_response_timeout_seconds)
        periodic_thread = threading.Thread(
            target=periodic_bms_sender,
            args=(config, active, raw_writer, collector),
            daemon=True,
        )
        periodic_thread.start()
        if config.interactive:
            interactive_thread = threading.Thread(
                target=stdin_sender,
                args=(config, active, raw_writer),
                daemon=True,
            )
            interactive_thread.start()

        while not active.stop_requested.is_set():
            try:
                monitor_once(config, raw_writer, collector, active)
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
        description="Capture raw Zephyr Telnet logs and periodic BMS samples."
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

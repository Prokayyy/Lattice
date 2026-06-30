"""Supervise the Lattice live runner.

The live runner owns trading decisions. This process owns liveness: it starts
the runner, watches the heartbeat file, restarts on crash/stall, and sends a
Telegram alert when monitoring is unhealthy.
"""
import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from discovery.notify import LatticeNotifier


HEARTBEAT = os.path.join(ROOT, "discovery", "live_runner_heartbeat.json")
LOG = os.path.join("/dev/shm", "lattice-live-runner.log")


def now():
    return time.time()


def load_heartbeat():
    try:
        with open(HEARTBEAT, "r") as f:
            return json.load(f)
    except Exception:
        return {}


async def send_alert(text):
    try:
        await LatticeNotifier().text(text)
    except Exception as exc:
        print(f"supervisor alert failed: {exc}", flush=True)


class Supervisor:
    def __init__(self, runner_args):
        self.runner_args = runner_args
        self.process = None
        self.stopping = False
        self.last_alert = {}
        self.restart_count = 0

    def should_alert(self, key):
        cooldown = max(
            float(config.LATTICE_SUPERVISOR_ALERT_COOLDOWN_SECONDS),
            0,
        )
        last = self.last_alert.get(key, 0)
        if now() - last < cooldown:
            return False
        self.last_alert[key] = now()
        return True

    async def alert(self, key, text):
        if self.should_alert(key):
            await send_alert(text)

    def start_runner(self):
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        log = open(LOG, "a", buffering=1)
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "discovery.live_runner",
            *self.runner_args,
        ]
        self.process = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.restart_count += 1
        print(
            f"supervisor started runner pid={self.process.pid} "
            f"restart_count={self.restart_count}",
            flush=True,
        )

    def stop_runner(self):
        if self.process is None or self.process.poll() is not None:
            return

        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except Exception:
            self.process.terminate()

        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except Exception:
                self.process.kill()
            self.process.wait(timeout=10)

    async def restart(self, reason):
        await self.alert(
            f"restart:{reason}",
            f"runner watchdog restart: {reason}",
        )
        self.stop_runner()
        delay = max(
            float(config.LATTICE_SUPERVISOR_RESTART_DELAY_SECONDS),
            0,
        )
        if delay:
            await asyncio.sleep(delay)
        self.start_runner()

    def heartbeat_age(self):
        hb = load_heartbeat()
        ts = float(hb.get("time") or 0)
        if ts <= 0:
            try:
                ts = os.path.getmtime(HEARTBEAT)
            except OSError:
                return None, hb
        return now() - ts, hb

    async def loop(self):
        self.start_runner()
        await self.alert(
            "supervisor_started",
            "runner supervisor started",
        )

        while not self.stopping:
            await asyncio.sleep(
                max(float(config.LATTICE_SUPERVISOR_CHECK_SECONDS), 1)
            )

            if self.process is None:
                await self.restart("process_missing")
                continue

            code = self.process.poll()
            if code is not None:
                await self.restart(f"process_exit:{code}")
                continue

            age, hb = self.heartbeat_age()
            stale_after = max(
                float(config.LATTICE_RUNNER_STALE_SECONDS),
                30,
            )
            if age is None:
                await self.restart("heartbeat_missing")
                continue

            if age > stale_after:
                status = hb.get("status", "unknown")
                await self.restart(
                    f"heartbeat_stale:{int(age)}s:{status}"
                )

    async def shutdown(self):
        self.stopping = True
        await self.alert("supervisor_stopping", "runner supervisor stopping")
        self.stop_runner()


async def main():
    parser = argparse.ArgumentParser()
    args, runner_args = parser.parse_known_args()
    # Pass runner args straight through. With no explicit --min-conviction the
    # runner derives the floor from the deployed model's recommended cutoff;
    # the systemd unit may still pass --min-conviction to pin it.

    supervisor = Supervisor(runner_args)
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(supervisor.shutdown()),
            )
        except NotImplementedError:
            pass

    await supervisor.loop()


if __name__ == "__main__":
    asyncio.run(main())

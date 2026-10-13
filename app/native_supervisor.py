"""Linux native workload containment; only verified PID identities may be signaled."""
import asyncio
import ctypes
from contextvars import ContextVar
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager

class NativeError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

class NativeSupervisor:
    def __init__(self):
        if sys.platform != "linux" or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal") or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("Linux pidfd and subreaper support required")
        self._slot = asyncio.Lock()
        self._reservation = ContextVar("native_reservation", default=None)
        self._active_reservation = None
        self._running_task = None
        self._tasks = set()
        self._closed = False
        self._poisoned = False
        self.grace_seconds = 1
        self.last_execution = None

    @property
    def poisoned(self):
        return self._poisoned

    @asynccontextmanager
    async def reservation(self, queue_timeout):
        if self._closed or self._poisoned:
            raise NativeError("NATIVE_UNAVAILABLE", "Native processing unavailable.")
        try:
            await asyncio.wait_for(self._slot.acquire(), queue_timeout)
        except asyncio.TimeoutError as error:
            raise NativeError("NATIVE_QUEUE_BUSY", "Native processing queue is busy.") from error
        task = asyncio.current_task()
        self._tasks.add(task)
        identity = object()
        self._active_reservation = identity
        token = self._reservation.set(identity)
        try:
            if self._closed or self._poisoned:
                raise NativeError("NATIVE_UNAVAILABLE", "Native processing unavailable.")
            yield
        finally:
            self._tasks.discard(task)
            self._reservation.reset(token)
            if not self._poisoned:
                self._active_reservation = None
                self._slot.release()

    @staticmethod
    def _processes():
        records = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                records[int(entry.name)] = (int(fields[1]), int(fields[2]), int(fields[19]))
            except (FileNotFoundError, ProcessLookupError):
                continue
        return records

    @staticmethod
    def _cgroup(pid):
        return Path(f"/proc/{pid}/cgroup").read_text()

    def _initialize_worker(self, process, baseline):
        process._native_baseline = baseline
        process._native_owned = {}
        records = self._processes()
        leader = records.get(process.pid)
        if leader is not None and leader[0] == os.getpid() and leader[1] == process.pid:
            try:
                if self._cgroup(process.pid) != self._cgroup(os.getpid()):
                    self._poisoned = True
                    raise NativeError("NATIVE_CONTAINMENT_FAILED", "Worker cgroup identity differs.")
                process._native_owned[process.pid] = leader[2]
            except FileNotFoundError:
                if process.pid in self._processes():
                    self._poisoned = True
                    raise NativeError("NATIVE_CONTAINMENT_FAILED", "Worker identity could not be confirmed.")

    def _track(self, process):
        records = self._processes()
        owned = process._native_owned
        changed = True
        while changed:
            changed = False
            live_owned = {pid for pid, born in owned.items()
                          if pid in records and records[pid][2] == born}
            group_is_owned = any(records[pid][1] == process.pid for pid in live_owned)
            for pid, (parent, group, born) in records.items():
                # Ancestry requires the parent's *current* starttime identity.
                # Group attribution requires a live verified anchor, so recycled
                # parent PIDs and a later unrelated PGID cannot confer ownership.
                if parent in live_owned or (group_is_owned and group == process.pid):
                    try:
                        if self._cgroup(pid) != self._cgroup(os.getpid()):
                            self._poisoned = True
                            raise NativeError("NATIVE_CONTAINMENT_FAILED", "Worker escaped its cgroup.")
                    except FileNotFoundError:
                        continue
                    if pid not in owned:
                        owned[pid] = born
                        changed = True
        for pid, (parent, group, born) in records.items():
            if parent == os.getpid() and (pid, born) not in process._native_baseline and owned.get(pid) != born:
                process._native_unattributed = True
        return {pid: born for pid, born in owned.items()
                if pid in records and records[pid][2] == born}

    async def _contain(self, process):
        try:
            await self._contain_inner(process)
        except BaseException as error:
            self._poisoned = True
            if isinstance(error, NativeError):
                raise
            raise NativeError("NATIVE_CONTAINMENT_FAILED", "Native termination could not be verified.") from error

    async def _contain_inner(self, process):
        def send(sig):
            owned = self._track(process)
            for pid, born in owned.items():
                # PIDFD prevents PID reuse between identity verification and kill.
                try:
                    fd = os.pidfd_open(pid)
                    try:
                        records = self._processes()
                        if pid in records and records[pid][2] == born and self._cgroup(pid) == self._cgroup(os.getpid()):
                            signal.pidfd_send_signal(fd, sig)
                    finally:
                        os.close(fd)
                except (ProcessLookupError, FileNotFoundError):
                    records = self._processes()
                    if pid in records and records[pid][2] == born:
                        raise NativeError("NATIVE_CONTAINMENT_FAILED", "Native signal ownership could not be confirmed.")
        # Even successful parent exit must not leave descendants holding the slot.
        send(signal.SIGTERM)
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline:
            self._reap_descendants(process)
            if process.returncode is not None and not self._track(process):
                self._verify_attribution(process)
                return
            await asyncio.sleep(0.02)
        send(signal.SIGKILL)
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            self._poisoned = True
            raise NativeError("NATIVE_CONTAINMENT_FAILED", "Worker termination could not be confirmed.")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self._reap_descendants(process)
            if not self._track(process):
                self._verify_attribution(process)
                return
            send(signal.SIGKILL)
            await asyncio.sleep(0.02)
        self._poisoned = True
        raise NativeError("NATIVE_CONTAINMENT_FAILED", "Worker descendants did not terminate.")

    def _verify_attribution(self, process):
        if getattr(process, "_native_unattributed", False):
            self._poisoned = True
            raise NativeError("NATIVE_CONTAINMENT_FAILED", "A new child has unverifiable worker ancestry.")

    def _reap_descendants(self, process):
        # Do not reap the leader: asyncio's child watcher owns its wait status.
        for pid in self._track(process):
            if pid == process.pid:
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    async def run(self, command, *, cwd, timeout, env):
        if not self._slot.locked() or self._reservation.get() is not self._active_reservation:
            raise NativeError("NATIVE_UNAVAILABLE", "Native reservation required.")
        if not math.isfinite(timeout) or not 0 < timeout <= 180:
            raise ValueError("Native timeout must be finite and at most 180 seconds")
        if self._running_task is not None:
            raise NativeError("NATIVE_QUEUE_BUSY", "Native execution is already active.")
        process = None
        started = time.monotonic()
        with tempfile.TemporaryFile(dir=cwd) as stdout, tempfile.TemporaryFile(dir=cwd) as stderr:
            self._running_task = asyncio.current_task()
            try:
                baseline = {(pid, record[2]) for pid, record in self._processes().items() if record[0] == os.getpid()}
                launch = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=cwd,
                    stdout=stdout, stderr=stderr, start_new_session=True,
                    env={key:value for key,value in env.items() if key != "WORKPIECE_PROJECT_API_TOKEN"}))
                cancelled = False
                while not launch.done():
                    try:
                        await asyncio.shield(launch)
                    except asyncio.CancelledError:
                        cancelled = True
                    except OSError:
                        break
                try:
                    process = launch.result()
                except OSError as error:
                    raise NativeError("NATIVE_START_FAILED", "Native process could not start.") from error
                self._initialize_worker(process, baseline)
                if cancelled:
                    raise asyncio.CancelledError
                while process.returncode is None:
                    self._track(process)
                    if time.monotonic() - started >= timeout:
                        raise NativeError("NATIVE_TIMEOUT", "Native execution deadline exceeded.")
                    await asyncio.sleep(0.01)
                if time.monotonic() - started >= timeout:
                    raise NativeError("NATIVE_TIMEOUT", "Native execution deadline exceeded.")
                await self._contain(process)
                outputs = []
                for stream in (stdout, stderr):
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - 1024 * 1024))
                    outputs.append(stream.read().decode("utf-8", errors="replace"))
                return subprocess.CompletedProcess(command, process.returncode, *outputs)
            finally:
                if process is not None:
                    cleanup = asyncio.create_task(self._contain(process))
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    try:
                        await cleanup
                    except BaseException:
                        self._poisoned = True
                        raise
                    self.last_execution = {"pid":process.pid,"duration_seconds":time.monotonic()-started,"terminated":True}
                if not self._poisoned:
                    self._running_task = None

    async def shutdown(self):
        self._closed = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

"""The worker dies with the app, because it belongs to the core's Job Object.

The shell puts the core in a Windows Job Object that kills what is in it when the last handle to the
job closes (`app/src-tauri/src/core_process.rs`: KILL_ON_JOB_CLOSE, no breakaway). A process another
process starts joins the job its parent is in unless it is started with CREATE_BREAKAWAY_FROM_JOB,
which the worker is not. This test builds such a job, puts a stand-in for the core in it, has the
stand-in start a worker the way the core does (`connect_process`), and closes the job: the stand-in
and the worker both have to be gone. (The Rust side has a test of its own that the core is in the
job; this is the half that the worker's start adds.)

It says nothing of a frozen build, whose executable is started the same way but is not tested here.
"""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows")

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

STAND_IN = """
import asyncio, sys
from raphael_core.background.host import connect_process

async def main():
    sys.stdin.readline()  # the test has put this process in the job: go
    link = await connect_process([sys.executable, "-c", "import time; time.sleep(120)"])
    print(link.process.pid, flush=True)
    await asyncio.sleep(120)

asyncio.run(main())
"""


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]  # fmt: skip


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimits),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def new_job():
    job = kernel32.CreateJobObjectW(None, None)
    assert job, "could not create a Job Object"
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
    )
    assert ok, "could not set up the Job Object"
    return job


def waits_for(pid):
    """A handle to wait on the process `pid`, or None if it is already gone."""
    return kernel32.OpenProcess(SYNCHRONIZE, False, pid) or None


def is_running(handle):
    return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT


def test_the_worker_a_process_in_a_job_starts_dies_when_the_job_closes():
    job = new_job()
    stand_in = subprocess.Popen(
        [sys.executable, "-c", STAND_IN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    handles = []
    try:
        process = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE, False, stand_in.pid)
        assert process
        handles.append(process)
        if not kernel32.AssignProcessToJobObject(job, process):
            pytest.skip(f"this process may not be put in a job here (error {ctypes.get_last_error()})")
        stand_in.stdin.write("go\n")
        stand_in.stdin.flush()
        worker_pid = int(stand_in.stdout.readline())  # the worker the stand-in started
        worker = waits_for(worker_pid)
        assert worker, "the worker is not running"
        handles.append(worker)
        assert is_running(process) and is_running(worker)

        kernel32.CloseHandle(job)  # the last handle to the job: what the OS does when the app exits
        job = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (is_running(process) or is_running(worker)):
            time.sleep(0.1)
        assert not is_running(process), "the stand-in for the core outlived the job"
        assert not is_running(worker), "the worker outlived the job: it is not in the core's job"
    finally:
        if job:
            kernel32.CloseHandle(job)
        stand_in.kill()
        stand_in.wait(timeout=10)
        for stream in (stand_in.stdin, stand_in.stdout):
            stream.close()
        for handle in handles:
            kernel32.CloseHandle(handle)

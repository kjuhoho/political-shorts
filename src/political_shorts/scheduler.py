"""Register / remove Windows Task Scheduler jobs that run the pipeline.

Uses schtasks.exe so no admin rights are needed for per-user tasks. Multiple
daily "slots" are supported (e.g. a morning and an evening run), each its own
task named ``PoliticalShorts<Slot>``.

Tasks are created from a full XML definition so they are robust:
  * StartWhenAvailable  — if the PC was off/asleep at the scheduled time, run
    as soon as it is next available (this is the default schtasks tasks lack)
  * runs on battery, does not stop on battery
  * needs a network connection
  * 30-minute execution limit
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from .config import ROOT
from .logging_setup import get_logger

log = get_logger("scheduler")

TASK_PREFIX = "PoliticalShorts"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>political-shorts: {desc}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Count>2</Count>
      <Interval>PT10M</Interval>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_name(slot: str) -> str:
    slot = (slot or "").strip()
    return TASK_PREFIX + (slot[:1].upper() + slot[1:] if slot else "Daily")


def _python() -> str:
    """The real console python — NOT pythonw. A scheduled run must be able to
    write its stdout/stderr to a log file; pythonw discards both, which is how a
    hung-on-network run once died silently at the time limit."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def _runner_script() -> Path:
    return ROOT / "scripts" / "run_pipeline.py"


def _current_user() -> str:
    dom = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{dom}\\{user}" if dom else user


def register(
    time_str: str = "07:30",
    slot: str = "",
    *,
    collect: bool = True,
    publish: bool = False,
    max_items: int | None = None,
) -> int:
    """Create (or overwrite) one robust daily task. Returns the schtasks exit code."""
    py = _python()
    runner = _runner_script()
    logfile = ROOT / "logs" / "schtask.log"
    (ROOT / "logs").mkdir(exist_ok=True)
    run_parts = [f'"{py}"', "-X", "utf8", f'"{runner}"']
    if not collect:
        run_parts.append("--no-collect")
    if publish:
        run_parts.append("--publish")
    if max_items:
        run_parts.append(f"--max {int(max_items)}")
    name = _task_name(slot)

    # Wrap in cmd.exe so stdout+stderr (incl. any traceback) are appended to a
    # log file — a scheduled run that fails must leave a trail.
    inner = " ".join(run_parts) + f' >> "{logfile}" 2>&1'
    command = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    arguments = f'/c "{inner}"'

    hh, mm = (time_str.split(":") + ["00"])[:2]
    xml = _TASK_XML.format(
        desc=escape(f"{name} (publish={publish}, max={max_items})"),
        time=f"{int(hh):02d}:{int(mm):02d}",
        user=escape(_current_user()),
        command=escape(command),
        arguments=escape(arguments),
        workdir=escape(str(ROOT)),
    )
    xml_file = Path(tempfile.gettempdir()) / f"{name}.xml"
    xml_file.write_text(xml, encoding="utf-16")

    cmd = ["schtasks", "/Create", "/F", "/TN", name, "/XML", str(xml_file)]
    log.info("registering task %s @ %s (publish=%s max=%s)", name, time_str, publish, max_items)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    try:
        xml_file.unlink()
    except OSError:
        pass
    if proc.returncode == 0:
        log.info("task '%s' scheduled daily at %s", name, time_str)
    return proc.returncode


def unregister(slot: str = "") -> int:
    name = _task_name(slot)
    proc = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", name], capture_output=True, text=True
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def _all_task_names() -> list[str]:
    """Every scheduled task whose name starts with the project prefix."""
    proc = subprocess.run(
        ["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True, text=True
    )
    names: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.startswith('"'):
            continue
        first = line.split('","', 1)[0].strip('"').lstrip("\\")
        if first.startswith(TASK_PREFIX) and first not in names:
            names.append(first)
    return names


def unregister_all() -> int:
    names = _all_task_names()
    if not names:
        print("no PoliticalShorts* tasks found")
        return 0
    rc = 0
    for name in names:
        p = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", name], capture_output=True, text=True
        )
        sys.stdout.write(p.stdout)
        sys.stderr.write(p.stderr)
        rc = rc or p.returncode
    return rc


def status() -> str:
    names = _all_task_names()
    if not names:
        return "no PoliticalShorts* tasks registered"
    out: list[str] = []
    for name in names:
        p = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"],
            capture_output=True, text=True,
        )
        out.append(p.stdout or p.stderr)
    return "\n".join(out)

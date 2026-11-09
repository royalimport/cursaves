"""Hands-off background sync: Windows Scheduled Task + synced Cursor launcher."""

import base64
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

TASK_NAME = "cursaves autosync"
SHORTCUT_NAME = "Cursor (synced).lnk"
LOG_PATH = Path.home() / ".config" / "cursaves" / "autosync.log"
LOG_MAX_BYTES = 1_000_000
MAX_AGE_DAYS = 30
SYNC_ARGS = ["sync", "--background", "--include-new", "--max-age-days", str(MAX_AGE_DAYS)]

# Environment as it was before background mode tweaked it, so Cursor launched
# from here doesn't inherit the no-prompt git settings in its terminals.
_ORIGINAL_ENV = dict(os.environ)


def setup_background_logging(label: str) -> None:
    """Send all output to the autosync log and stop git from prompting."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
        LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))

    log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log

    # No console to answer a prompt, so fail fast instead of hanging.
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    os.environ["GCM_INTERACTIVE"] = "never"

    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} cursaves {label} ===")


def _require_windows(action: str) -> bool:
    if platform.system() == "Windows":
        return True
    print(
        f"'autosync {action}' is Windows-only.\n"
        "On macOS/Linux, schedule 'cursaves sync --background' with cron or launchd.",
        file=sys.stderr,
    )
    return False


def _cursaves_command() -> list[str]:
    """Command that runs this cursaves install, independent of PATH at task time."""
    exe = shutil.which("cursaves")
    if exe:
        return [os.path.abspath(exe)]
    return [sys.executable, "-m", "cursor_saves"]


def _headless_action(args: list[str]) -> tuple[str, str]:
    """Wrap a command in a headless console so neither it nor git flashes a window."""
    conhost = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe"
    parts = ["--headless"] + _cursaves_command() + args
    return str(conhost), subprocess.list2cmdline(parts)


def find_cursor_exe() -> Optional[Path]:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor" / "Cursor.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cursor" / "Cursor.exe",
    ]
    cli = shutil.which("cursor")
    if cli:
        # <install>\resources\app\bin\cursor.cmd
        candidates.append(Path(cli).resolve().parents[3] / "Cursor.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _current_user_sid() -> str:
    out = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return out.split(",")[-1].strip('"')


def _task_xml(command: str, arguments: str, user_sid: str, interval_minutes: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Syncs Cursor chats via cursaves. Imports only while Cursor is closed. Log: {escape(str(LOG_PATH))}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user_sid)}</UserId>
      <Delay>PT1M</Delay>
    </LogonTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>{datetime.now():%Y-%m-%dT%H:%M:%S}</StartBoundary>
      <Repetition>
        <Interval>PT{interval_minutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user_sid)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True,
    )


def _shortcut_path() -> Path:
    return (
        Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / SHORTCUT_NAME
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_shortcut(cursor_exe: Path) -> bool:
    command, arguments = _headless_action(["autosync", "launch"])
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"{_ps_quote(str(_shortcut_path()))});"
        f"$s.TargetPath = {_ps_quote(command)};"
        f"$s.Arguments = {_ps_quote(arguments)};"
        f"$s.IconLocation = {_ps_quote(str(cursor_exe) + ',0')};"
        f"$s.WorkingDirectory = {_ps_quote(str(Path.home()))};"
        "$s.Description = 'Sync Cursor chats, then open Cursor';"
        "$s.Save()"
    )
    result = _run_powershell(script)
    if result.returncode != 0:
        print(f"  Shortcut failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def install(interval_minutes: int = 15, shortcut: bool = True) -> int:
    if not _require_windows("install"):
        return 1

    command, arguments = _headless_action(SYNC_ARGS)
    xml = _task_xml(command, arguments, _current_user_sid(), interval_minutes)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", delete=False, encoding="utf-16"
    ) as f:
        f.write(xml)
        xml_path = f.name
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(xml_path)

    if result.returncode != 0:
        print(f"Failed to create scheduled task: {result.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"Scheduled task '{TASK_NAME}' installed:")
    print(f"  Runs at logon and every {interval_minutes} min, with no visible window")
    print("  Imports only while Cursor is closed; pushes any time")
    print(f"  Log: {LOG_PATH}")

    if shortcut:
        cursor_exe = find_cursor_exe()
        if not cursor_exe:
            print("  Cursor.exe not found; skipped the 'Cursor (synced)' shortcut")
        elif _create_shortcut(cursor_exe):
            print(f"  Start Menu shortcut: {SHORTCUT_NAME[:-4]} (syncs, then opens Cursor)")

    print("\nRun 'cursaves autosync status' to check it, or start a run now with:")
    print(f'  schtasks /Run /TN "{TASK_NAME}"')
    return 0


def uninstall() -> int:
    if not _require_windows("uninstall"):
        return 1

    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Removed scheduled task '{TASK_NAME}'")
    else:
        print(f"Scheduled task not removed: {result.stderr.strip()}", file=sys.stderr)

    lnk = _shortcut_path()
    if lnk.exists():
        lnk.unlink()
        print(f"Removed Start Menu shortcut '{SHORTCUT_NAME[:-4]}'")
    return 0


def status(lines: int = 25) -> int:
    if platform.system() == "Windows":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"Scheduled task '{TASK_NAME}' is not installed.")
        else:
            wanted = ("Status:", "Last Run Time:", "Last Result:", "Next Run Time:", "Repeat: Every:")
            for line in result.stdout.splitlines():
                if line.strip().startswith(wanted):
                    print(line.strip())
        print(f"Shortcut: {'installed' if _shortcut_path().exists() else 'not installed'}")

    print(f"\nLog: {LOG_PATH}")
    if not LOG_PATH.exists():
        print("  (no runs logged yet)")
        return 0
    tail = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    for line in tail:
        print(f"  {line}")
    return 0


def launch(run_sync) -> int:
    """Sync while Cursor is closed, then start Cursor."""
    from .importer import is_cursor_running

    setup_background_logging("autosync launch")

    cursor_exe = find_cursor_exe()
    if not cursor_exe:
        print("Cursor.exe not found", file=sys.stderr)
        return 1

    if not is_cursor_running():
        try:
            run_sync()
        except SystemExit:
            pass
        except Exception as e:
            print(f"Sync failed, opening Cursor anyway: {e}", file=sys.stderr)

    flags = 0
    if platform.system() == "Windows":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(cursor_exe)], env=_ORIGINAL_ENV, cwd=str(Path.home()),
        creationflags=flags, close_fds=True,
    )
    print(f"Started {cursor_exe}")
    return 0

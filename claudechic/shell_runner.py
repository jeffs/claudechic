"""PTY-based shell command execution with color support.

NOTE: PTY support is Unix-only. On Windows, run_in_pty raises NotImplementedError.
Use the fallback in commands.py for Windows (interactive shell via subprocess).
"""

import os
import subprocess
import sys
from typing import TYPE_CHECKING

# PTY support is Unix-only
UNIX_PTY_SUPPORT = sys.platform != "win32"

if UNIX_PTY_SUPPORT or TYPE_CHECKING:
    import pty
    import select


def run_in_pty(
    cmd: str, shell: str, cwd: str | None, env: dict[str, str]
) -> tuple[str, int]:
    """Run command in PTY to capture colors.

    Returns (output, returncode) tuple.

    Raises NotImplementedError on Windows (PTY not available).
    """
    if not UNIX_PTY_SUPPORT:
        raise NotImplementedError("PTY shell execution is not available on Windows")

    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            [shell, "-lc", cmd],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)

        output = b""
        while True:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        output += data
                    else:
                        break
                except OSError:
                    break
            elif proc.poll() is not None:
                # Process done, drain remaining output
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.05)
                    if not r:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            output += data
                        else:
                            break
                    except OSError:
                        break
                break

        os.close(master_fd)
        proc.wait()
        return output.decode(errors="replace"), proc.returncode or 0
    except Exception:
        os.close(master_fd)
        raise

"""CLI Formatter and Report Renderer with ANSI support.

ANSI colors are automatically disabled when:
  - stdout is not a TTY (pipe, file redirect, CI runner)
  - Running on Windows without ANSI terminal support
  - NO_COLOR environment variable is set
"""

import os
import sys
import shutil


class ANSIFormatter:
    """ANSI color formatter that auto-detects TTY and disables in non-interactive contexts."""

    def __init__(self, force_color: bool = False, force_plain: bool = False):
        if force_plain:
            self.use_color = False
        elif force_color:
            self.use_color = True
        else:
            self.use_color = self._supports_color()

    def _supports_color(self) -> bool:
        """Return True only when stdout is an interactive TTY that supports ANSI."""
        # Respect NO_COLOR convention: https://no-color.org/
        if os.environ.get("NO_COLOR"):
            return False
        # Not a real TTY (piped/redirected)
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False
        # Windows: only support color when a compatible ANSI terminal is present
        if os.name == "nt":
            return (
                os.environ.get("TERM") in ("xterm", "xterm-256color")
                or os.environ.get("ANSICON") is not None
                or os.environ.get("WT_SESSION") is not None  # Windows Terminal
            )
        return True

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.use_color else text

    def red(self, text: str) -> str:
        return f"\033[31m{text}\033[0m" if self.use_color else text

    def green(self, text: str) -> str:
        return f"\033[32m{text}\033[0m" if self.use_color else text

    def yellow(self, text: str) -> str:
        return f"\033[33m{text}\033[0m" if self.use_color else text

    def cyan(self, text: str) -> str:
        return f"\033[36m{text}\033[0m" if self.use_color else text

    def header(self, title: str) -> str:
        term_width = min(shutil.get_terminal_size((80, 20)).columns, 80)
        line = "=" * term_width
        centered = title.center(term_width)
        return f"{self.cyan(line)}\n{self.bold(centered)}\n{self.cyan(line)}"

    def subheader(self, title: str) -> str:
        term_width = min(shutil.get_terminal_size((80, 20)).columns, 80)
        line = "-" * term_width
        return f"\n{self.cyan(line)}\n[{self.bold(title)}]\n{self.cyan(line)}"

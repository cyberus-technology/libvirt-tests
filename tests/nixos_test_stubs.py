
"""
Stub definitions for NixOS test framework to help with linting.
This file should be in the same directory as your test script.
"""
from typing import Any, Optional, List, Dict, Union, TYPE_CHECKING

class Machine:
    """Stub for NixOS test machine object."""

    def start(self) -> None:
        """Start the virtual machine."""
        pass

    def shutdown(self) -> None:
        """Shutdown the virtual machine."""
        pass

    def crash(self) -> None:
        """Crash the virtual machine."""
        pass

    def block(self) -> None:
        """Block until the machine stops."""
        pass

    def succeed(self, command: str, timeout: Optional[int] = None) -> str:
        """Execute command and expect success."""
        return ""

    def fail(self, command: str, timeout: Optional[int] = None) -> str:
        """Execute command and expect failure."""
        return ""

    def execute(self, command: str, timeout: Optional[int] = None) -> tuple[int, str]:
        """Execute command and return (exit_code, output)."""
        return (0, "")

    def wait_for_unit(self, unit: str, timeout: Optional[int] = None) -> None:
        """Wait for systemd unit to be active."""
        pass

    def wait_until_succeeds(self, command: str, timeout: Optional[int] = None) -> None:
        """Wait until command succeeds."""
        pass

    def wait_until_fails(self, command: str, timeout: Optional[int] = None) -> None:
        """Wait until command fails."""
        pass

    def wait_for_console_text(self, text: str, timeout: Optional[int] = None) -> None:
        """Wait for text to appear on console."""
        pass

    def send_console(self, text: str) -> None:
        """Send text to console."""
        pass

    def wait_for_x(self) -> None:
        """Wait for X server to start."""
        pass

    def wait_for_text(self, text: str, timeout: Optional[int] = None) -> None:
        """Wait for text to appear on screen."""
        pass

    def wait_for_window(self, window: str, timeout: Optional[int] = None) -> None:
        """Wait for window to appear."""
        pass

    def send_key(self, key: str) -> None:
        """Send key to the machine."""
        pass

    def send_chars(self, chars: str) -> None:
        """Send characters to the machine."""
        pass

    def screenshot(self, filename: str) -> None:
        """Take a screenshot."""
        pass

    def copy_from_host(self, source: str, target: str) -> None:
        """Copy file from host to machine."""
        pass

    def copy_from_vm(self, source: str, target: str) -> None:
        """Copy file from machine to host."""
        pass

    def systemctl(self, command: str, unit: str = "") -> str:
        """Run systemctl command."""
        return ""

    def get_unit_info(self, unit: str) -> Dict[str, Any]:
        """Get systemd unit information."""
        return {}


# Global machine instances that will be available in test scripts
# Add your machine names here based on your test configuration
controllerVM: Machine = Machine()
computeVM: Machine = Machine()

# Additional NixOS test utilities
def start_all() -> None:
    """Start all machines."""
    pass

def create_machine(config: Dict[str, Any]) -> Machine:
    """Create a new machine."""
    return Machine()

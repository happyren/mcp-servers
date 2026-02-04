"""PID file management for orphan process cleanup.

Tracks spawned processes via PID files to enable cleanup after crashes.
"""

import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger("telegram_controller.pid_manager")


class PIDManager:
    """Manages PID files for process tracking and orphan cleanup."""
    
    def __init__(self, pids_dir: Path):
        """Initialize PID manager.
        
        Args:
            pids_dir: Directory to store PID files
        """
        self.pids_dir = pids_dir
        self.pids_dir.mkdir(parents=True, exist_ok=True)
    
    def write_pid(self, instance_id: str, pid: int) -> None:
        """Write PID file for orphan tracking.
        
        Args:
            instance_id: Instance ID
            pid: Process ID
        """
        pid_file = self.pids_dir / f"{instance_id}.pid"
        try:
            pid_file.write_text(str(pid), encoding="utf-8")
            logger.debug(f"Wrote PID file for instance {instance_id[:8]}: {pid}")
        except Exception as e:
            logger.error(f"Failed to write PID file for {instance_id[:8]}: {e}")
    
    def remove_pid(self, instance_id: str) -> None:
        """Remove PID file when instance stops.
        
        Args:
            instance_id: Instance ID
        """
        pid_file = self.pids_dir / f"{instance_id}.pid"
        try:
            if pid_file.exists():
                pid_file.unlink()
                logger.debug(f"Removed PID file for instance {instance_id[:8]}")
        except Exception as e:
            logger.error(f"Failed to remove PID file for {instance_id[:8]}: {e}")
    
    def read_pid(self, instance_id: str) -> Optional[int]:
        """Read PID from file.
        
        Args:
            instance_id: Instance ID
            
        Returns:
            PID if file exists and is valid, None otherwise
        """
        pid_file = self.pids_dir / f"{instance_id}.pid"
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None
    
    @staticmethod
    def is_process_running(pid: int) -> bool:
        """Check if a process is still running.
        
        Args:
            pid: Process ID
            
        Returns:
            True if process exists and is running
        """
        try:
            # Signal 0 checks if process exists without actually sending a signal
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    
    def cleanup_orphans(self, managed_pids: Set[int]) -> int:
        """Clean up orphan processes from previous crashed sessions.
        
        Scans the pids directory for leftover PID files, checks if those
        processes are still running, and terminates them.
        
        Args:
            managed_pids: Set of PIDs that are currently managed (don't kill these)
        
        Returns:
            Number of orphan processes cleaned up
        """
        cleaned = 0
        
        if not self.pids_dir.exists():
            return 0
        
        for pid_file in self.pids_dir.glob("*.pid"):
            instance_id = pid_file.stem
            
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError) as e:
                logger.warning(f"Invalid PID file {pid_file.name}: {e}")
                pid_file.unlink(missing_ok=True)
                continue
            
            # Skip currently managed processes
            if pid in managed_pids:
                continue
            
            # Check if process is still running
            if self.is_process_running(pid):
                logger.warning(f"Found orphan process PID {pid} (instance {instance_id[:8]}), terminating...")
                try:
                    # Try graceful termination first
                    os.kill(pid, signal.SIGTERM)
                    
                    # Wait briefly for it to exit
                    for _ in range(10):  # 1 second total
                        if not self.is_process_running(pid):
                            break
                        time.sleep(0.1)
                    
                    # Force kill if still running
                    if self.is_process_running(pid):
                        logger.warning(f"Orphan PID {pid} did not exit, force killing")
                        os.kill(pid, signal.SIGKILL)
                    
                    logger.info(f"Cleaned up orphan process PID {pid}")
                    cleaned += 1
                except OSError as e:
                    logger.error(f"Failed to kill orphan PID {pid}: {e}")
            else:
                logger.debug(f"PID file {pid_file.name} refers to dead process, cleaning up")
            
            # Remove the stale PID file
            pid_file.unlink(missing_ok=True)
        
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} orphan process(es)")
        
        return cleaned

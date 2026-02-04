"""Port allocation for OpenCode instances.

Manages port allocation within a defined range.
"""

import logging
import socket
from typing import Set

logger = logging.getLogger("telegram_controller.port_allocator")

# Default port range
DEFAULT_PORT_RANGE_START = 4097  # Start at 4097, leaving 4096 for current opencode session
DEFAULT_PORT_RANGE_END = 4200


class PortAllocator:
    """Manages port allocation for OpenCode instances."""
    
    def __init__(
        self,
        port_range_start: int = DEFAULT_PORT_RANGE_START,
        port_range_end: int = DEFAULT_PORT_RANGE_END,
    ):
        """Initialize port allocator.
        
        Args:
            port_range_start: Start of port range (inclusive)
            port_range_end: End of port range (exclusive)
        """
        self.port_range_start = port_range_start
        self.port_range_end = port_range_end
        self.used_ports: Set[int] = set()
    
    @staticmethod
    def is_port_available(port: int) -> bool:
        """Check if a port is actually available on the system.
        
        Args:
            port: Port number to check
            
        Returns:
            True if port is available for binding
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('127.0.0.1', port))
                return True
        except OSError:
            return False
    
    def allocate(self) -> int:
        """Allocate an unused port.
        
        Scans the port range and checks both internal tracking and actual
        system availability.
        
        Returns:
            Allocated port number
            
        Raises:
            RuntimeError: If no ports are available
        """
        for port in range(self.port_range_start, self.port_range_end):
            if port in self.used_ports:
                continue
            if self.is_port_available(port):
                self.used_ports.add(port)
                logger.debug(f"Allocated port {port}")
                return port
        raise RuntimeError(
            f"No available ports in range {self.port_range_start}-{self.port_range_end}"
        )
    
    def allocate_specific(self, port: int) -> int:
        """Try to allocate a specific port, or allocate a new one if unavailable.
        
        Args:
            port: Desired port number
            
        Returns:
            Allocated port (either the requested one or a new one)
        """
        if not self.is_port_available(port):
            logger.warning(f"Specified port {port} is not available, allocating new port")
            return self.allocate()
        
        if port not in self.used_ports:
            self.used_ports.add(port)
        
        return port
    
    def release(self, port: int) -> None:
        """Release a port when an instance stops.
        
        Args:
            port: Port to release
        """
        self.used_ports.discard(port)
        logger.debug(f"Released port {port}")
    
    def mark_used(self, port: int) -> None:
        """Mark a port as used (e.g., when loading state).
        
        Args:
            port: Port to mark as used
        """
        self.used_ports.add(port)

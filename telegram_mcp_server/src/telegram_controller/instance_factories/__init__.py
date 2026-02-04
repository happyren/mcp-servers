"""Instance factories for multi-bot controller support.

This package provides factory abstractions for different agent types:
- OpenCode instances
- QuantCode instances
- Custom HTTP proxy instances
"""

from .base import InstanceFactory
from .opencode_factory import OpenCodeInstanceFactory
from .quantcode_factory import QuantCodeInstanceFactory
from .registry import FactoryRegistry, get_registry, reset_registry

__all__ = [
    "InstanceFactory",
    "OpenCodeInstanceFactory",
    "QuantCodeInstanceFactory",
    "FactoryRegistry",
    "get_registry",
    "reset_registry",
]

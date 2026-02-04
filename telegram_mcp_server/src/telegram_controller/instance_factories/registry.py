"""Factory registry for multi-bot support.

This module provides a registry for dynamically loading and managing
instance factories based on configuration.
"""

import importlib
import logging
from typing import Dict, Optional, Type

from .base import InstanceFactory
from .opencode_factory import OpenCodeInstanceFactory
from .quantcode_factory import QuantCodeInstanceFactory

logger = logging.getLogger(__name__)


# Built-in factories
_BUILTIN_FACTORIES: Dict[str, Type[InstanceFactory]] = {
    "opencode": OpenCodeInstanceFactory,
    "quantcode": QuantCodeInstanceFactory,
}


class FactoryRegistry:
    """Registry for instance factories.
    
    Manages loading and caching of factories, supporting both built-in
    factories and custom factories loaded from configuration.
    
    Example usage:
        registry = FactoryRegistry()
        
        # Register from config
        registry.register_from_config({
            "opencode": {
                "factory": "telegram_controller.instance_factories.OpenCodeInstanceFactory",
                "default_config": {"provider_id": "deepseek"}
            }
        })
        
        # Get factory by type
        factory = registry.get("opencode")
        instance = await factory.create(...)
    """
    
    def __init__(self):
        """Initialize the registry with built-in factories."""
        self._factories: Dict[str, InstanceFactory] = {}
        self._factory_classes: Dict[str, Type[InstanceFactory]] = {}
        self._default_configs: Dict[str, Dict] = {}
        
        # Register built-in factories
        for name, factory_class in _BUILTIN_FACTORIES.items():
            self._factory_classes[name] = factory_class
    
    def register(
        self,
        instance_type: str,
        factory: InstanceFactory,
        default_config: Optional[Dict] = None,
    ) -> None:
        """Register a factory instance.
        
        Args:
            instance_type: Type identifier (e.g., 'opencode')
            factory: Factory instance
            default_config: Default configuration for this type
        """
        self._factories[instance_type] = factory
        if default_config:
            self._default_configs[instance_type] = default_config
        logger.debug(f"Registered factory for type: {instance_type}")
    
    def register_class(
        self,
        instance_type: str,
        factory_class: Type[InstanceFactory],
        default_config: Optional[Dict] = None,
    ) -> None:
        """Register a factory class (lazy instantiation).
        
        Args:
            instance_type: Type identifier
            factory_class: Factory class
            default_config: Default configuration for this type
        """
        self._factory_classes[instance_type] = factory_class
        if default_config:
            self._default_configs[instance_type] = default_config
        logger.debug(f"Registered factory class for type: {instance_type}")
    
    def register_from_config(self, instance_types: Dict[str, Dict]) -> None:
        """Register factories from configuration dictionary.
        
        Args:
            instance_types: Dict mapping type name to config with 'factory' and 'default_config'
            
        Example config:
            {
                "opencode": {
                    "factory": "telegram_controller.instance_factories.OpenCodeInstanceFactory",
                    "default_config": {"provider_id": "deepseek"}
                }
            }
        """
        for type_name, type_config in instance_types.items():
            factory_path = type_config.get("factory", "")
            default_config = type_config.get("default_config", {})
            
            # Check if it's a built-in factory
            if type_name in _BUILTIN_FACTORIES:
                self._factory_classes[type_name] = _BUILTIN_FACTORIES[type_name]
                if default_config:
                    self._default_configs[type_name] = default_config
                continue
            
            # Try to load custom factory class
            if factory_path:
                try:
                    factory_class = self._load_factory_class(factory_path)
                    self._factory_classes[type_name] = factory_class
                    if default_config:
                        self._default_configs[type_name] = default_config
                    logger.info(f"Loaded custom factory for {type_name}: {factory_path}")
                except Exception as e:
                    logger.error(f"Failed to load factory {factory_path}: {e}")
    
    def _load_factory_class(self, class_path: str) -> Type[InstanceFactory]:
        """Load a factory class from a dotted path.
        
        Args:
            class_path: Fully qualified class path (e.g., 'module.submodule.ClassName')
            
        Returns:
            Factory class
        """
        parts = class_path.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid class path: {class_path}")
        
        module_path, class_name = parts
        
        try:
            module = importlib.import_module(module_path)
            factory_class = getattr(module, class_name)
            
            if not issubclass(factory_class, InstanceFactory):
                raise TypeError(f"{class_path} is not a subclass of InstanceFactory")
            
            return factory_class
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not load factory class {class_path}: {e}")
    
    def get(self, instance_type: str) -> Optional[InstanceFactory]:
        """Get a factory by instance type.
        
        Args:
            instance_type: Type identifier
            
        Returns:
            Factory instance, or None if not found
        """
        # Check if already instantiated
        if instance_type in self._factories:
            return self._factories[instance_type]
        
        # Lazy instantiation
        if instance_type in self._factory_classes:
            factory = self._factory_classes[instance_type]()
            self._factories[instance_type] = factory
            return factory
        
        logger.warning(f"No factory registered for type: {instance_type}")
        return None
    
    def get_default_config(self, instance_type: str) -> Dict:
        """Get default configuration for an instance type.
        
        Args:
            instance_type: Type identifier
            
        Returns:
            Default configuration dictionary
        """
        return self._default_configs.get(instance_type, {})
    
    def list_types(self) -> list[str]:
        """List all registered instance types."""
        return list(set(self._factories.keys()) | set(self._factory_classes.keys()))
    
    def has_type(self, instance_type: str) -> bool:
        """Check if an instance type is registered."""
        return instance_type in self._factories or instance_type in self._factory_classes


# Global registry instance
_registry: Optional[FactoryRegistry] = None


def get_registry() -> FactoryRegistry:
    """Get the global factory registry."""
    global _registry
    if _registry is None:
        _registry = FactoryRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = None

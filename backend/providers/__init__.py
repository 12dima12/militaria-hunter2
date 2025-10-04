"""
Provider registry for managing multiple auction/marketplace providers
"""
from typing import List, Dict
from .base import BaseProvider
from .militaria321 import Militaria321Provider
from .egun import EgunProvider

# Registry of all available providers
_PROVIDER_REGISTRY: Dict[str, BaseProvider] = {}


def register_provider(provider: BaseProvider):
    """Register a provider in the registry"""
    _PROVIDER_REGISTRY[provider.name] = provider


def get_provider(name: str) -> BaseProvider:
    """Get a provider by name"""
    return _PROVIDER_REGISTRY.get(name)


def get_all_providers() -> List[BaseProvider]:
    """Get all registered providers in deterministic order (alphabetical)"""
    return [_PROVIDER_REGISTRY[name] for name in sorted(_PROVIDER_REGISTRY.keys())]


def get_provider_names() -> List[str]:
    """Get all provider names in deterministic order (alphabetical)"""
    return sorted(_PROVIDER_REGISTRY.keys())


# Initialize and register all providers
def initialize_providers():
    """Initialize all providers and register them"""
    # Clear existing registry
    _PROVIDER_REGISTRY.clear()
    
    # Register providers in alphabetical order for deterministic behavior
    register_provider(EgunProvider())
    register_provider(Militaria321Provider())
    
    return get_all_providers()


# Auto-initialize on import
initialize_providers()


__all__ = [
    'BaseProvider',
    'Militaria321Provider',
    'EgunProvider',
    'get_provider',
    'get_all_providers',
    'get_provider_names',
    'initialize_providers',
    'register_provider',
]
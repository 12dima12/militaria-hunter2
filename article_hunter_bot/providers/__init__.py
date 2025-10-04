"""Provider registry for extensible platform support"""

from typing import Dict, List
from providers.base import BaseProvider
from providers.militaria321 import Militaria321Provider


def get_all_providers() -> Dict[str, BaseProvider]:
    """Get all registered providers in deterministic order"""
    providers = {
        "militaria321.com": Militaria321Provider(),
        # Future providers can be added here
        # "egun.de": EgunProvider(),
    }
    return providers


def get_provider_names() -> List[str]:
    """Get provider names in alphabetical order"""
    return sorted(get_all_providers().keys())

"""
Centralized listing key utilities for stable, canonical identification.

A listing key uniquely identifies an item across polls and should NEVER change.
Format: "{platform}:{platform_id}" where platform_id is the numeric ID.
"""
import re
import logging

logger = logging.getLogger(__name__)


def build_listing_key(platform: str, url: str) -> str:
    """
    Build a stable, canonical listing key from platform and URL.
    
    This is the SINGLE SOURCE OF TRUTH for listing identification.
    
    Args:
        platform: Provider name (e.g., "militaria321.com", "egun.de")
        url: Item URL
        
    Returns:
        Canonical key format: "{platform}:{platform_id}"
        
    Raises:
        ValueError: If platform_id cannot be extracted
    """
    platform_id = extract_platform_id(platform, url)
    if not platform_id:
        raise ValueError(f"Could not extract platform_id from URL: {url}")
    
    return f"{platform}:{platform_id}"


def extract_platform_id(platform: str, url: str) -> str:
    """
    Extract canonical numeric platform_id from URL.
    
    Platform-specific extraction rules:
    - militaria321.com: /auktion/{id}/ or ?id={id}
    - egun.de: item.php?id={id}
    
    Args:
        platform: Provider name
        url: Item URL
        
    Returns:
        Numeric platform_id as string, or empty string if not found
    """
    if "militaria321" in platform.lower():
        # militaria321 patterns
        patterns = [
            r'/auktion/(\d+)',  # Primary: /auktion/7580057/...
            r'[?&]id=(\d+)',    # Query param: ?id=123
            r'/(\d{7,})',       # Fallback: any 7+ digit number
        ]
    elif "egun" in platform.lower():
        # egun patterns
        patterns = [
            r'[?&]id=(\d+)',    # Primary: item.php?id=20104003
            r'/(\d{7,})',       # Fallback: any 7+ digit number
        ]
    else:
        # Generic fallback
        patterns = [
            r'[?&]id=(\d+)',
            r'/item/(\d+)',
            r'/product/(\d+)',
            r'/listing/(\d+)',
            r'/(\d{7,})',
        ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            platform_id = match.group(1)
            logger.debug(f"Extracted platform_id={platform_id} from {url} using pattern {pattern}")
            return platform_id
    
    logger.warning(f"Could not extract platform_id from URL: {url} (platform: {platform})")
    return ""


def parse_listing_key(listing_key: str) -> tuple[str, str]:
    """
    Parse a listing key into platform and platform_id.
    
    Args:
        listing_key: Format "{platform}:{platform_id}"
        
    Returns:
        (platform, platform_id)
    """
    parts = listing_key.split(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", ""

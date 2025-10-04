from typing import List, Dict, Any
import logging
from datetime import datetime, timedelta
import asyncio

from models import Listing, StoredListing, KeywordHit, Notification, Keyword
from database import DatabaseManager
from providers import get_all_providers
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class SearchService:
    """Service for searching across auction platforms"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        # Initialize providers from registry (in deterministic order)
        all_providers = get_all_providers()
        self.providers = {provider.name: provider for provider in all_providers}
        logger.info(f"Initialized SearchService with providers: {list(self.providers.keys())}")
        self.notification_service = NotificationService(db_manager)
    
    async def search_keyword(self, keyword: Keyword) -> Dict[str, Any]:
        """Search for a keyword and notify only for truly new listings"""
        results = {
            "keyword_id": keyword.id,
            "keyword_text": keyword.keyword,
            "new_notifications": 0,
            "matched_listings": 0,
            "total_raw_listings": 0,
            "errors": []
        }
        
        try:
            # Search each platform
            all_raw_listings = []
            
            for platform in keyword.platforms:
                if platform in self.providers:
                    try:
                        provider = self.providers[platform]
                        search_result = await provider.search(keyword.keyword, sample_mode=False)
                        all_raw_listings.extend(search_result.items)
                        logger.debug(f"Raw search found {len(search_result.items)} listings for '{keyword.keyword}' on {platform}")
                        
                    except Exception as e:
                        error_msg = f"Error searching {platform}: {str(e)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                else:
                    error_msg = f"Provider not found for platform: {platform}"
                    results["errors"].append(error_msg)
                    logger.warning(error_msg)
            
            results["total_raw_listings"] = len(all_raw_listings)
            
            # Apply title-only matching to get relevant listings
            matched_listings = []
            provider = self.providers[keyword.platforms[0]]  # Use first platform's provider for matching
            
            for listing in all_raw_listings:
                if provider.matches_keyword(listing.title, keyword.keyword):
                    matched_listings.append(listing)
            
            results["matched_listings"] = len(matched_listings)
            logger.info(f"Keyword '{keyword.keyword}': {len(all_raw_listings)} raw -> {len(matched_listings)} matched")
            
            # Check for truly new listings (not in seen_set and posted after since_ts)
            new_notifications = []
            
            from services.keyword_service import KeywordService
            keyword_service = KeywordService(self.db)
            
            for listing in matched_listings:
                listing_key = keyword_service.make_listing_key(listing.platform, listing.platform_id)
                
                # Skip if already seen
                if keyword_service.is_listing_seen(keyword, listing.platform, listing.platform_id):
                    continue
                
                # Check if listing was posted after subscription start
                # Since we don't have posting timestamp from militaria321, assume first_seen is posting time
                is_new_posting = True
                if listing.first_seen_ts and listing.first_seen_ts <= keyword.since_ts:
                    is_new_posting = False
                
                # For listings without timestamp, consider them new if not in seen_set
                if is_new_posting or not listing.first_seen_ts:
                    # Store listing in database
                    stored_listing = StoredListing(
                        platform=listing.platform,
                        platform_id=listing.platform_id,
                        title=listing.title,
                        url=listing.url,
                        price_value=listing.price_value,
                        price_currency=listing.price_currency,
                        location=listing.location,
                        condition=listing.condition,
                        seller_name=listing.seller_name,
                        seller_rating=listing.seller_rating,
                        listing_type=listing.listing_type,
                        image_url=listing.image_url,
                        first_seen_ts=listing.first_seen_ts or datetime.utcnow(),
                        last_seen_ts=listing.last_seen_ts or datetime.utcnow()
                    )
                    
                    await self.db.create_or_update_listing(stored_listing)
                    new_notifications.append(stored_listing)
                    
                    # Add to seen_set
                    await keyword_service.add_to_seen_set(keyword.id, listing.platform, listing.platform_id)
                    
                    logger.info(f"New listing to notify: {listing.title}")
                
                else:
                    # Add to seen_set even if we don't notify (it's an old item we just discovered)
                    await keyword_service.add_to_seen_set(keyword.id, listing.platform, listing.platform_id)
            
            results["new_notifications"] = len(new_notifications)
            
            # Send notifications for truly new listings only
            if new_notifications and not keyword.is_muted:
                await self._send_notifications(keyword, new_notifications)
            
            # Update last checked timestamp
            await keyword_service.update_last_checked(keyword.id)
            
        except Exception as e:
            error_msg = f"Error searching keyword '{keyword.keyword}': {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
        
        return results
    
    async def _send_notifications(self, keyword: Keyword, listings: List[StoredListing]):
        """Send notifications for new listings"""
        try:
            # Get user
            user = await self.db.get_user_by_id(keyword.user_id)
            if not user:
                logger.error(f"User not found for keyword {keyword.id}")
                return
            
            # Send notifications
            for listing in listings:
                await self.notification_service.send_listing_notification(
                    user, keyword, listing
                )
                
        except Exception as e:
            logger.error(f"Error sending notifications: {e}")
    
    async def search_all_active_keywords(self) -> Dict[str, Any]:
        """Search all active keywords"""
        results = {
            "keywords_processed": 0,
            "total_new_listings": 0,
            "errors": [],
            "start_time": datetime.utcnow()
        }
        
        try:
            # Get all active keywords
            keywords = await self.db.get_all_active_keywords()
            logger.info(f"Processing {len(keywords)} active keywords")
            
            # Process keywords in batches to avoid overwhelming providers
            batch_size = 5
            for i in range(0, len(keywords), batch_size):
                batch = keywords[i:i + batch_size]
                tasks = [self.search_keyword(keyword) for keyword in batch]
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for keyword, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        error_msg = f"Exception processing keyword '{keyword.keyword}': {str(result)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                    else:
                        results["keywords_processed"] += 1
                        results["total_new_listings"] += result.get("new_listings", 0)
                        results["errors"].extend(result.get("errors", []))
                
                # Small delay between batches
                if i + batch_size < len(keywords):
                    await asyncio.sleep(2)
            
            results["end_time"] = datetime.utcnow()
            results["duration_seconds"] = (results["end_time"] - results["start_time"]).total_seconds()
            
            logger.info(f"Processed {results['keywords_processed']} keywords, "
                       f"found {results['total_new_listings']} new listings in "
                       f"{results['duration_seconds']:.1f} seconds")
            
        except Exception as e:
            error_msg = f"Error in search_all_active_keywords: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
        
        return results
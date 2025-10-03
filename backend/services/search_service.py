from typing import List, Dict, Any
import logging
from datetime import datetime, timedelta
import asyncio

from models import Listing, StoredListing, KeywordHit, Notification, Keyword
from database import DatabaseManager
from providers.militaria321 import Militaria321Provider
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class SearchService:
    """Service for searching across auction platforms"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.providers = {
            "militaria321.com": Militaria321Provider()
        }
        self.notification_service = NotificationService(db_manager)
    
    async def search_keyword(self, keyword: Keyword) -> Dict[str, Any]:
        """Search for a keyword across its platforms"""
        results = {
            "keyword_id": keyword.id,
            "keyword_text": keyword.keyword,
            "new_listings": 0,
            "total_listings": 0,
            "errors": []
        }
        
        try:
            # Calculate since timestamp (last check or 1 hour ago)
            since_ts = keyword.last_checked
            if not since_ts:
                since_ts = datetime.utcnow() - timedelta(hours=1)
            
            all_listings = []
            
            # Search each platform
            for platform in keyword.platforms:
                if platform in self.providers:
                    try:
                        provider = self.providers[platform]
                        search_result = await provider.search(keyword.keyword, since_ts, sample_mode=False)
                        all_listings.extend(search_result.items)
                        logger.info(f"Found {len(listings)} listings for '{keyword.keyword}' on {platform}")
                        
                    except Exception as e:
                        error_msg = f"Error searching {platform}: {str(e)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                else:
                    error_msg = f"Provider not found for platform: {platform}"
                    results["errors"].append(error_msg)
                    logger.warning(error_msg)
            
            results["total_listings"] = len(all_listings)
            
            # Process listings
            new_listings = []
            for listing in all_listings:
                try:
                    # Convert to StoredListing
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
                        first_seen_ts=listing.first_seen_ts,
                        last_seen_ts=listing.last_seen_ts
                    )
                    
                    # Check if listing is new
                    existing = await self.db.get_listing_by_platform_id(
                        listing.platform, listing.platform_id
                    )
                    
                    if not existing:
                        # New listing
                        await self.db.create_or_update_listing(stored_listing)
                        new_listings.append(stored_listing)
                        
                        # Create keyword hit
                        hit = KeywordHit(
                            keyword_id=keyword.id,
                            listing_id=stored_listing.id,
                            user_id=keyword.user_id
                        )
                        await self.db.create_keyword_hit(hit)
                        
                        logger.info(f"New listing found: {listing.title}")
                    else:
                        # Update existing listing
                        await self.db.create_or_update_listing(stored_listing)
                        
                        # Check if we already have a hit for this keyword+listing
                        hit_exists = await self.db.keyword_hit_exists(keyword.id, existing.id)
                        if not hit_exists:
                            # Create new hit (listing appeared again)
                            hit = KeywordHit(
                                keyword_id=keyword.id,
                                listing_id=existing.id,
                                user_id=keyword.user_id
                            )
                            await self.db.create_keyword_hit(hit)
                            new_listings.append(existing)
                
                except Exception as e:
                    error_msg = f"Error processing listing: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
            
            results["new_listings"] = len(new_listings)
            
            # Send notifications for new listings
            if new_listings and not keyword.is_muted:
                await self._send_notifications(keyword, new_listings)
            
            # Update last checked timestamp
            from services.keyword_service import KeywordService
            keyword_service = KeywordService(self.db)
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
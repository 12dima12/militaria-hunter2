#!/usr/bin/env python3
"""
Demo script to show logging output from deep pagination
"""
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
import os
import sys
import unicodedata

# Add current directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from models import Keyword
from services.search_service import SearchService

# Load environment
load_dotenv()

# Configure detailed logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def demo_logging():
    """Demo the structured logging from deep pagination"""
    
    # Initialize database
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    # Initialize search service
    search_service = SearchService(db_manager)
    
    print("🔍 Deep Pagination Logging Demo - Keyword: 'medaille'")
    print("=" * 60)
    print("Watch for structured logs showing:")
    print("• poll_start - Shows mode and pages to scan")
    print("• m321_page - Per-page results")  
    print("• poll_summary - Final statistics")
    print("• decision - Item-level push/absorb decisions")
    print("=" * 60)
    print()
    
    # Test with existing medaille keyword
    test_keyword = "medaille"
    normalized = unicodedata.normalize('NFKC', test_keyword.lower())
    
    # Look for existing keyword
    existing_keywords = await db_manager.get_user_keywords("test_user_id", active_only=False)
    test_keyword_obj = None
    
    for kw in existing_keywords:
        if kw.normalized_keyword == normalized:
            test_keyword_obj = kw
            break
    
    if not test_keyword_obj:
        # Create test keyword with rotate mode for interesting logs
        test_keyword_obj = Keyword(
            user_id="test_user_id",
            original_keyword=test_keyword,
            normalized_keyword=normalized,
            since_ts=datetime.utcnow(),
            poll_mode="rotate",
            poll_window=3,  # Smaller window for demo
            poll_cursor_page=2,  # Start at page 2 
            total_pages_estimate=25  # Estimate for good rotation
        )
        test_keyword_obj = await db_manager.create_keyword(test_keyword_obj)
        logger.info(f"Created demo keyword with cursor_page=2, window=3")
    
    # Update cursor for demo
    await db_manager.db.keywords.update_one(
        {"id": test_keyword_obj.id},
        {"$set": {"poll_cursor_page": 8, "poll_window": 3, "total_pages_estimate": 25}}
    )
    
    # Reload keyword
    doc = await db_manager.db.keywords.find_one({"id": test_keyword_obj.id})
    test_keyword_obj = Keyword(**doc)
    
    print(f"Demo Setup:")
    print(f"• Keyword: {test_keyword_obj.original_keyword}")
    print(f"• Mode: {test_keyword_obj.poll_mode}")
    print(f"• Cursor: {test_keyword_obj.poll_cursor_page}")
    print(f"• Window: {test_keyword_obj.poll_window}")
    print(f"• Estimate: {test_keyword_obj.total_pages_estimate}")
    print(f"• Expected pages: [1] + [8, 9, 10] = [1, 8, 9, 10]")
    print()
    print("🚀 Starting deep polling (watch logs)...")
    print()
    
    try:
        # Run deep polling - should show logs for pages 1, 8, 9, 10
        results = await search_service.search_keyword(test_keyword_obj, dry_run=False)
        
        print()
        print(f"✅ Deep polling completed!")
        print(f"📈 New items found: {len(results)}")
        print()
        
        # Show final cursor position
        doc = await db_manager.db.keywords.find_one({"id": test_keyword_obj.id})
        updated_keyword = Keyword(**doc)
        print(f"📊 Final Stats:")
        print(f"• Cursor advanced to: {updated_keyword.poll_cursor_page}")
        print(f"• Seen keys total: {len(updated_keyword.seen_listing_keys)}")
        
        if results:
            print(f"• Sample new items:")
            for i, item in enumerate(results[:2]):
                print(f"  {i+1}. {item.title[:50]}... - {item.price_value}€")
        
    except Exception as e:
        print(f"❌ Deep polling failed: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("🎉 Logging Demo Complete!")
    
    # Cleanup
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(demo_logging())
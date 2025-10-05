#!/usr/bin/env python3
"""
Debug the complete polling flow to find where seen_listing_keys go wrong
"""

import asyncio
import sys
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from models import User, Keyword

async def debug_poll_flow():
    """Debug the complete flow"""
    
    print("=== Debugging Poll Flow ===\n")
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    search_service = SearchService(db_manager)
    
    try:
        # 1. Create a test keyword
        test_keyword = Keyword(
            id="debug_test_keyword",
            user_id="debug_user",
            original_keyword="testdebug",
            normalized_keyword="testdebug", 
            since_ts=datetime.utcnow(),
            baseline_status="complete",
            platforms=["militaria321.com"],
            seen_listing_keys=[
                "militaria321.com:7196810",  # These are from the logs
                "militaria321.com:5044904"
            ]
        )
        
        # Delete any existing test keyword
        await db_manager.db.keywords.delete_many({"id": "debug_test_keyword"})
        
        # Create the test keyword
        await db_manager.create_keyword(test_keyword)
        print(f"✓ Created test keyword with {len(test_keyword.seen_listing_keys)} seen keys")
        
        # 2. Reload from database to confirm it was saved
        doc = await db_manager.db.keywords.find_one({"id": "debug_test_keyword"})
        reloaded = Keyword(**doc)
        print(f"✓ Reloaded keyword has {len(reloaded.seen_listing_keys)} seen keys")
        print(f"  First few keys: {reloaded.seen_listing_keys[:3]}")
        
        # 3. Test the search logic directly
        print(f"\n--- Testing search logic ---")
        new_items = await search_service.search_keyword(reloaded)
        print(f"✓ Search returned {len(new_items)} new items")
        
        # 4. Check what happened to seen keys after search
        doc = await db_manager.db.keywords.find_one({"id": "debug_test_keyword"})
        final_keyword = Keyword(**doc)
        print(f"✓ Final keyword has {len(final_keyword.seen_listing_keys)} seen keys")
        
        # 5. Clean up
        await db_manager.db.keywords.delete_one({"id": "debug_test_keyword"})
        print(f"✓ Cleaned up test keyword")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(debug_poll_flow())
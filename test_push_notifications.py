#!/usr/bin/env python3
"""
Test push notifications by creating a new keyword that should find items
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from models import User, Keyword

async def test_push_notifications():
    """Test push notifications by creating a fresh keyword"""
    
    print("=== Testing Push Notifications ===\n")
    
    os.environ['DB_NAME'] = 'article_hunter_test'
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    try:
        # Create a test user
        test_user = User(
            id="push_test_user",
            telegram_id=999999999  # Fake telegram ID for testing
        )
        
        # Delete existing test user/keyword
        await db_manager.db.users.delete_many({"id": "push_test_user"})
        await db_manager.db.keywords.delete_many({"user_id": "push_test_user"})
        
        # Create test user
        await db_manager.create_user(test_user)
        print("✓ Created test user")
        
        # Create a keyword that should find items
        test_keyword = Keyword(
            id="push_test_keyword", 
            user_id="push_test_user",
            original_keyword="reich",  # Should find many items
            normalized_keyword="reich",
            since_ts=datetime.utcnow(),  # Set to now - all existing items should be "old"
            baseline_status="complete",  # Skip baseline
            platforms=["militaria321.com"],
            seen_listing_keys=[]  # Empty - so all items will be "new"
        )
        
        await db_manager.create_keyword(test_keyword)
        print("✓ Created test keyword 'reich' with empty seen_listing_keys")
        print(f"  Since TS: {test_keyword.since_ts}")
        print(f"  All existing items should be considered 'old' (posted before since_ts)")
        print(f"  But since seen_listing_keys is empty, they should trigger grace window logic")
        
        # The keyword is now ready for polling
        # The next poll should find items, see they're not in seen_listing_keys,
        # but they should be filtered as "old" due to since_ts being newer than posted_ts
        
        # Wait and check results
        print(f"\nWait 60-120 seconds then check bot logs for:")
        print(f"  - grep 'reich.*decision' bot_fixed.log")
        print(f"  - grep 'reich.*poll_summary' bot_fixed.log") 
        
        print(f"\nIf grace window logic works, items without posted_ts should be pushed")
        print(f"if they're found within 60 minutes of since_ts")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(test_push_notifications())
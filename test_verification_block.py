#!/usr/bin/env python3
"""
Test verification block by simulating a /search command
"""

import asyncio
import sys
import os
from datetime import datetime

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from models import User, Keyword

async def simulate_search_command():
    """Simulate a /search command to test verification block"""
    
    print("=== Simulating /search Command for Verification Block ===\n")
    
    os.environ['DB_NAME'] = 'article_hunter_test'
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    search_service = SearchService(db_manager)
    
    try:
        # Create test user and keyword for verification
        test_user = User(
            id="verify_test_user", 
            telegram_id=888888888
        )
        
        # Clean up any existing test data
        await db_manager.db.users.delete_many({"id": "verify_test_user"})
        await db_manager.db.keywords.delete_many({"user_id": "verify_test_user"})
        
        await db_manager.create_user(test_user)
        print("✓ Created test user")
        
        # Create a fresh keyword that will find items
        test_keyword = Keyword(
            id="verify_test_keyword",
            user_id="verify_test_user", 
            original_keyword="messer",  # Should find items
            normalized_keyword="messer",
            since_ts=datetime.utcnow(),
            baseline_status="pending",
            platforms=["militaria321.com"]
        )
        
        await db_manager.create_keyword(test_keyword)
        print("✓ Created test keyword 'messer'")
        
        # Run baseline (this will generate the verification block data)
        print("\n🔍 Running baseline crawl...")
        baseline_items, last_item_meta = await search_service.full_baseline_seed(
            "messer", test_keyword.id
        )
        
        print(f"✓ Baseline completed: {len(baseline_items)} items found")
        
        if last_item_meta and last_item_meta.get("listing"):
            print(f"✓ Last item metadata captured from page {last_item_meta['page_index']}")
            
            # Import the verification block formatter
            sys.path.insert(0, '/app/article_hunter_bot')
            from simple_bot import _format_verification_block
            
            verification_text = await _format_verification_block(
                last_item_meta, "messer", search_service
            )
            
            print("\n" + "="*60)
            print("VERIFICATION BLOCK OUTPUT:")
            print("="*60)
            print(verification_text)
            print("="*60)
            
        else:
            print("❌ No last item metadata captured")
        
        # Clean up
        await db_manager.db.users.delete_one({"id": "verify_test_user"})
        await db_manager.db.keywords.delete_one({"id": "verify_test_keyword"})
        print(f"\n✓ Cleaned up test data")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(simulate_search_command())
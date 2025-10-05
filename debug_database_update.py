#!/usr/bin/env python3
"""
Test if database updates are working properly
"""

import asyncio
import sys

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager

async def test_db_update():
    """Test database update functionality"""
    
    print("=== Testing Database Update ===\n")
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    try:
        # Find the existing keyword
        keyword_id = "demo_keyword_posted_ts"  # From earlier tests
        
        print("1. Current keyword state:")
        doc = await db_manager.db.keywords.find_one({"normalized_keyword": "orden"})
        if doc:
            keyword_id = doc["id"]
            print(f"   ID: {keyword_id}")
            print(f"   Seen keys: {len(doc.get('seen_listing_keys', []))}")
            
            # Test updating seen keys
            test_keys = [
                "militaria321.com:7196810", 
                "militaria321.com:5044904",
                "militaria321.com:test123"
            ]
            
            print(f"\n2. Updating with {len(test_keys)} test keys...")
            await db_manager.update_keyword_seen_keys(keyword_id, test_keys)
            
            print("3. Checking update result:")
            updated_doc = await db_manager.db.keywords.find_one({"id": keyword_id})
            if updated_doc:
                print(f"   Seen keys after update: {len(updated_doc.get('seen_listing_keys', []))}")
                print(f"   Keys: {updated_doc.get('seen_listing_keys', [])}")
                print(f"   Updated at: {updated_doc.get('updated_at', 'N/A')}")
            
            # Test if the update persists
            print(f"\n4. Testing persistence (re-query):")
            persistent_doc = await db_manager.db.keywords.find_one({"id": keyword_id})
            if persistent_doc:
                print(f"   Seen keys persist: {len(persistent_doc.get('seen_listing_keys', []))}")
                
                # Clear them back to 0 
                print(f"\n5. Clearing keys back to 0...")
                await db_manager.update_keyword_seen_keys(keyword_id, [])
                
                final_doc = await db_manager.db.keywords.find_one({"id": keyword_id})
                print(f"   Final seen keys: {len(final_doc.get('seen_listing_keys', []))}")
        else:
            print("   No 'orden' keyword found")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(test_db_update())
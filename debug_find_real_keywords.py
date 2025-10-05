#!/usr/bin/env python3
"""
Find the actual keywords being used by the bot
"""

import asyncio
import sys
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager

async def find_real_keywords():
    """Find keywords by searching different criteria"""
    
    print("=== Finding Real Keywords ===\n")
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    try:
        # 1. Search for all keywords (no filters)
        print("1. ALL keywords in database:")
        cursor = db_manager.db.keywords.find({})
        all_docs = await cursor.to_list(length=None)
        print(f"   Total: {len(all_docs)}")
        
        for doc in all_docs:
            print(f"   - {doc.get('normalized_keyword', 'N/A')} | {doc.get('user_id', 'N/A')} | {doc.get('since_ts', 'N/A')} | active: {doc.get('is_active', 'N/A')}")
        
        # 2. Search for keywords with since_ts around 12:24
        print(f"\n2. Keywords with since_ts around 12:24 (from logs):")
        target_time = datetime(2025, 10, 5, 12, 24, 14, 980000)  # From logs
        
        # Search within 1 hour window
        start_time = target_time.replace(hour=12, minute=0)
        end_time = target_time.replace(hour=13, minute=0)
        
        cursor = db_manager.db.keywords.find({
            "since_ts": {
                "$gte": start_time,
                "$lte": end_time
            }
        })
        time_docs = await cursor.to_list(length=None)
        print(f"   Found: {len(time_docs)}")
        
        for doc in time_docs:
            print(f"   - {doc.get('normalized_keyword', 'N/A')} | since_ts: {doc.get('since_ts', 'N/A')} | seen_keys: {len(doc.get('seen_listing_keys', []))}")
        
        # 3. Search by user patterns
        print(f"\n3. Keywords by user ID patterns:")
        cursor = db_manager.db.keywords.find({})
        all_docs = await cursor.to_list(length=None)
        
        user_groups = {}
        for doc in all_docs:
            user_id = doc.get('user_id', 'unknown')
            if user_id not in user_groups:
                user_groups[user_id] = []
            user_groups[user_id].append(doc)
        
        for user_id, docs in user_groups.items():
            print(f"   User '{user_id}': {len(docs)} keywords")
            for doc in docs[:3]:  # First 3 per user
                print(f"     - {doc.get('normalized_keyword', 'N/A')} | seen_keys: {len(doc.get('seen_listing_keys', []))}")
        
        # 4. Look for keywords with specific IDs from logs (militaria321.com:7196810)
        print(f"\n4. Keywords containing specific listing IDs from logs:")
        target_ids = ["militaria321.com:7196810", "militaria321.com:5044904"]
        
        for target_id in target_ids:
            cursor = db_manager.db.keywords.find({
                "seen_listing_keys": target_id
            })
            matching_docs = await cursor.to_list(length=None)
            print(f"   Keywords with '{target_id}': {len(matching_docs)}")
            for doc in matching_docs:
                print(f"     - {doc.get('normalized_keyword', 'N/A')} | user: {doc.get('user_id', 'N/A')}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(find_real_keywords())
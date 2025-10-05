#!/usr/bin/env python3
"""
Debug the seen_listing_keys to understand why no new items are being detected
"""

import asyncio
import sys
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager

async def debug_seen_keys():
    """Debug the seen listing keys"""
    
    print("=== Debugging Seen Listing Keys ===\n")
    
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    try:
        # Get ALL keywords (active and inactive)
        cursor = db_manager.db.keywords.find({})
        keyword_docs = await cursor.to_list(length=None)
        
        from models import Keyword
        keywords = [Keyword(**doc) for doc in keyword_docs]
        
        print(f"Total active keywords found: {len(keywords)}")
        
        # Search for keywords by exact attributes from logs
        target_keywords = []
        for keyword in keywords:
            print(f"Found keyword: {keyword.normalized_keyword}, since_ts: {keyword.since_ts}, user_id: {keyword.user_id}, active: {keyword.is_active}")
            if keyword.normalized_keyword in ['orden', 'sammlung', 'abzeichen']:
                target_keywords.append(keyword)
        
        print(f"\nTarget keywords matching log names: {len(target_keywords)}")
        
        for keyword in target_keywords[:5]:  # Check target keywords
            print(f"Keyword: {keyword.original_keyword}")
            print(f"  User ID: {keyword.user_id}")
            print(f"  Since TS: {keyword.since_ts}")
            print(f"  Baseline Status: {keyword.baseline_status}")
            print(f"  Seen Keys Count: {len(keyword.seen_listing_keys)}")
            
            # Show first few seen keys
            if keyword.seen_listing_keys:
                print("  First 10 seen keys:")
                for i, key in enumerate(keyword.seen_listing_keys[:10]):
                    print(f"    {i+1}: {key}")
                
                # Show last few seen keys
                print("  Last 10 seen keys:")
                for i, key in enumerate(keyword.seen_listing_keys[-10:]):
                    print(f"    {len(keyword.seen_listing_keys)-10+i+1}: {key}")
            
            print()
    
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        await db_manager.close()

if __name__ == "__main__":
    asyncio.run(debug_seen_keys())
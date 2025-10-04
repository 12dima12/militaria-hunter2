# Bot Restart Behavior - Persistence & State Management

## Summary

**The bot handles server restarts gracefully with full state persistence in MongoDB.**

## What Persists Across Restarts ✅

### 1. **Keywords & Subscriptions**
- All user keywords and their configurations
- Original keyword text and normalized version
- Subscription timestamps (`since_ts`)
- Status flags (`is_active`, `is_muted`)
- Frequency settings

### 2. **Baseline (seen_listing_keys)** 🔑
- **Critical**: The list of already-seen items per keyword
- Stored as: `["platform:platform_id", "platform:platform_id", ...]`
- Example: `["egun.de:20076341", "militaria321.com:6280926"]`
- **Purpose**: Prevents duplicate notifications after restart

### 3. **Monitoring State**
- `last_checked`: When each keyword was last searched
- `since_ts`: Subscription start time (never changes)
- User preferences and settings

### 4. **Database Indexes**
- All MongoDB indexes persist
- Ensures fast query performance after restart

## What Does NOT Persist ❌

### 1. **In-Memory Scheduler State**
- APScheduler job state is recreated on startup
- Jobs are rescheduled based on persisted `last_checked` timestamps

### 2. **Active Connections**
- HTTP client pools are recreated
- MongoDB connections are re-established
- Telegram bot connection is restarted

### 3. **Pending Tasks**
- Any in-flight searches are lost (will retry on next schedule)

## Restart Flow

```
┌─────────────────────────────────────────────────────────┐
│ 1. SERVER RESTART                                       │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 2. APPLICATION STARTUP                                  │
│    • Initialize MongoDB connection                      │
│    • Load all active keywords from database             │
│    • Initialize providers (egun.de, militaria321.com)   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 3. SCHEDULER INITIALIZATION                             │
│    • Create APScheduler instance                        │
│    • Add search job (runs every 60 seconds)             │
│    • Add cleanup job (daily at 2 AM)                    │
│    • Add health check job (every 5 minutes)             │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 4. FIRST SEARCH CYCLE AFTER RESTART                    │
│    For each active keyword:                             │
│    • Check: now >= (last_checked + frequency_seconds)   │
│    • If due, search all providers                       │
│    • Compare results against seen_listing_keys          │
│    • Only NEW items trigger notifications               │
└─────────────────────────────────────────────────────────┘
```

## Example Scenario

### Timeline:
```
10:00 AM - User creates /suche Pistole
           • Bot finds 10 existing items
           • Adds all 10 to seen_listing_keys
           • Saves to MongoDB: seen_listing_keys = ["egun.de:123", ...]

10:15 AM - New item appears on egun.de (ID: 456)

10:16 AM - Scheduled check runs
           • Finds 11 items (10 old + 1 new)
           • Compares against seen_listing_keys
           • Only item 456 is NEW → NOTIFIES user
           • Adds "egun.de:456" to seen_listing_keys
           • Updates MongoDB

10:30 AM - SERVER RESTARTS 🔄

10:31 AM - Bot comes back online
           • Loads keyword from MongoDB
           • seen_listing_keys = ["egun.de:123", ..., "egun.de:456"]
           • last_checked = 10:16 AM
           • Calculates next check due at 10:17 AM (already passed)

10:32 AM - First check after restart
           • Finds same 11 items
           • ALL 11 are in seen_listing_keys
           • NO notifications sent ✅

10:45 AM - Another new item appears (ID: 789)

10:46 AM - Scheduled check runs
           • Finds 12 items
           • Only item 789 is NEW → NOTIFIES user
           • Updates seen_listing_keys
```

## Edge Cases

### Long Downtime (Hours/Days)
```
Monday 10:00 AM  - /suche Helmet created
Monday 10:15 AM  - 5 new items appear → User notified
Monday 11:00 AM  - Server goes down for maintenance

Tuesday 10:00 AM - Server comes back online (23 hours later)
                 - Scheduler loads keyword from MongoDB
                 - seen_listing_keys intact with 5 items from Monday
                 
Tuesday 10:01 AM - First check after long downtime
                 - Finds 8 new items posted Monday-Tuesday
                 - Compares ALL against seen_listing_keys
                 - Only truly NEW items (not seen before) trigger notifications
                 - since_ts ensures chronological accuracy
```

### Rapid Restarts
```
If server restarts multiple times in quick succession:
• Each restart loads fresh state from MongoDB
• seen_listing_keys prevents duplicate notifications
• No "notification spam" even with repeated restarts
```

## Database Verification

Current state in MongoDB:
```javascript
// Example keyword document
{
  _id: ObjectId('...'),
  id: 'fa284035-d8e8-43fc-959a-9f63e143c755',
  user_id: '3cf1ea56-3a4a-4179-9ceb-196fba858d37',
  keyword: 'kappmesser',
  normalized_keyword: 'kappmesser',
  since_ts: ISODate('2025-10-04T11:45:03.331Z'),
  last_checked: ISODate('2025-10-04T12:34:02.898Z'),
  is_active: true,
  frequency_seconds: 60,
  seen_listing_keys: [
    'egun.de:20076341',
    'egun.de:20124703',
    'militaria321.com:6280926',
    'militaria321.com:7193849',
    'militaria321.com:8081487',
    'militaria321.com:8081490',
    'militaria321.com:7972442',
    'militaria321.com:7434108',
    'militaria321.com:7437015',
    'militaria321.com:6451182'
  ],
  platforms: ['egun.de', 'militaria321.com']
}
```

## Technical Implementation

### 1. Database Layer (`database.py`)
```python
# Keywords are stored with all state
await db.keywords.insert_one({
    "id": keyword.id,
    "user_id": keyword.user_id,
    "keyword": keyword.keyword,
    "seen_listing_keys": keyword.seen_listing_keys,  # Persisted!
    "since_ts": keyword.since_ts,
    "last_checked": keyword.last_checked,
    # ... other fields
})
```

### 2. Scheduler Layer (`scheduler.py`)
```python
async def _search_job(self):
    # Load ALL active keywords from MongoDB
    keywords = await self.db.get_all_active_keywords()
    
    for keyword in keywords:
        # Check if due for search
        if self._should_check_keyword(keyword, now):
            # Search and compare against persisted seen_listing_keys
            await self.search_service.search_keyword(keyword)
```

### 3. Search Service (`search_service.py`)
```python
async def search_keyword(self, keyword):
    # Get current results from providers
    results = await self.search_providers(keyword)
    
    # Filter out already-seen items
    new_items = [
        item for item in results 
        if f"{item.platform}:{item.platform_id}" not in keyword.seen_listing_keys
        and item.posted_after(keyword.since_ts)
    ]
    
    # Only notify about NEW items
    for item in new_items:
        await self.notification_service.send_notification(item)
        # Add to seen set
        keyword.seen_listing_keys.append(f"{item.platform}:{item.platform_id}")
    
    # Update database
    await self.db.update_keyword(keyword.id, {
        "seen_listing_keys": keyword.seen_listing_keys,
        "last_checked": datetime.utcnow()
    })
```

## User Experience

### ✅ What Users Notice:
- Continuous monitoring without interruption
- No duplicate notifications for old items
- Seamless service resumption

### ❌ What Users DON'T Notice:
- Server restarts (transparent)
- Database state reloading
- Scheduler reinitialization

## Monitoring & Logs

After restart, check logs for:
```
✓ "Database initialized: auction_bot_database"
✓ "Initialized SearchService with providers: ['egun.de', 'militaria321.com']"
✓ "Scheduler started"
✓ "Starting search job" (first check after restart)
✓ "Checking N keywords" (loaded from MongoDB)
```

## Conclusion

**The bot is production-ready for restarts:**
- ✅ Full state persistence in MongoDB
- ✅ Baseline (seen_listing_keys) prevents duplicate notifications
- ✅ Automatic scheduler resumption
- ✅ No manual intervention required
- ✅ Seamless user experience

**No data loss, no duplicate notifications, no downtime for users.**

# Deep Pagination Implementation Summary

## Overview
Successfully implemented deep pagination strategy for the Telegram Article Hunter bot to solve the critical issue where militaria321.com's end-date sorting caused new listings to be missed on deeper pages.

## Key Problem Solved
- **Issue**: Militaria321 sorts search results by auction end date, causing newly posted items to appear on later pages while older items bubble to page 1
- **Previous Behavior**: Bot only checked first 5 pages during 60s polling cycles
- **Result**: New items appearing on pages 6+ were completely missed

## Implementation Details

### 1. Enhanced Models (`models.py`)
Added poll-related fields to `Keyword` model:
```python
poll_cursor_page: int = 1           # Current page position in rotating deep-scan
total_pages_estimate: Optional[int] = None  # Estimated total pages for this keyword  
poll_mode: str = "rotate"           # "full" or "rotate"
poll_window: int = 5               # Number of pages in rotating window
last_deep_scan_at: Optional[datetime] = None  # Last full deep scan timestamp
```

Enhanced `SearchResult` model:
```python
last_page_index: Optional[int] = None  # Last page index processed
```

### 2. Provider Enhancements (`providers/militaria321.py`)
- Added support for `mode="poll"`, `poll_pages=1`, `page_start=N` parameters
- Single-page fetching capability for efficient deep scanning
- Enhanced metadata in search results (`pages_scanned`, `last_page_index`)
- Controlled throttling (200-600ms jitter between requests)

### 3. Deep Polling Strategy (`services/search_service.py`)

#### Two Polling Modes:
1. **Full-scan Mode** (`poll_mode="full"`):
   - Crawls ALL pages every 60s cycle
   - Suitable for smaller keywords (<40 pages)
   
2. **Rotating Deep-scan Mode** (`poll_mode="rotate"`, default):
   - Always scans PRIMARY_PAGES (typically page 1) for "front-page drift"
   - Additionally scans a rotating window of POLL_WINDOW pages
   - Cursor advances after each cycle, wrapping around at total_pages_estimate
   - Guarantees complete coverage over several minutes without overwhelming server

#### Configuration (Environment Variables):
```bash
POLL_MODE=rotate              # "full" or "rotate"
PRIMARY_PAGES=1               # Always scan these front pages each minute
POLL_WINDOW=5                 # Rotating window size
MAX_PAGES_PER_CYCLE=40        # Hard limit on pages per cycle
DETAIL_CONCURRENCY=4          # Max concurrent detail page fetches
GRACE_MINUTES=60              # Unchanged
POLL_INTERVAL_SECONDS=60      # Unchanged
```

### 4. Enhanced Bot UI (`simple_bot.py`)
Updated `/list` command to show poll telemetry:
```
📝 **medaille**
Status: ✅ Läuft — Letzte Prüfung erfolgreich
Letzte Prüfung: 05.10.2025 16:19 Uhr — Letzter Erfolg: 05.10.2025 16:19 Uhr
Baseline: complete
Plattformen: militaria321.com
Poll: Modus: rotate — Seiten: ~25 — Fenster: 11-13
```

### 5. Comprehensive Logging
Structured JSON logs for monitoring and debugging:

```json
// Poll cycle start
{"event": "poll_start", "keyword": "medaille", "mode": "rotate", "pages_to_scan": [1, 8, 9, 10], "cursor_start": 8, "window_size": 3}

// Per-page results  
{"event": "m321_page", "q": "medaille", "page_index": 8, "items_on_page": 25, "unseen_on_page": 3, "unique_total": 78}

// Poll cycle summary
{"event": "poll_summary", "keyword": "medaille", "mode": "rotate", "pages_scanned": 4, "primary_pages": 1, "cursor_start": 8, "window_size": 3, "unseen_candidates": 5, "pushed": 2, "absorbed": 3}

// Individual item decisions
{"event": "decision", "listing_key": "militaria321.com:12345", "posted_ts_utc": "2025-10-05T14:30:00", "decision": "pushed", "reason": "posted_ts>=since_ts"}
```

## Key Features

### Smart Page Selection
- **Rotating Windows**: Example with cursor=8, window=3, estimate=25 pages
  - Cycle 1: Scan pages [1, 8, 9, 10]  
  - Cycle 2: Scan pages [1, 11, 12, 13]
  - Cycle 3: Scan pages [1, 14, 15, 16]
  - Eventually wraps: [1, 1, 2, 3] (when cursor reaches end)

### Early Stop Optimization  
- Stops scanning after 5 consecutive empty pages (rotating window only)
- Prevents unnecessary requests when reaching end of results

### Backwards Compatibility
- Existing baseline behavior unchanged (fast, all pages, no detail fetch)
- Newness gating unchanged (posted_ts >= since_ts with 60min grace)
- All existing functionality preserved

### Site-Friendly Design
- Controlled concurrency (max 4 simultaneous detail page fetches)
- Adaptive throttling (200-600ms between pages)
- Proper HTTP headers and compression support

## Test Results

### Functional Testing
✅ **Page Selection Logic**: Correctly determines pages to scan for both modes  
✅ **Single Page Fetching**: Provider successfully fetches individual pages  
✅ **Deep Polling Integration**: End-to-end polling with cursor advancement  
✅ **Structured Logging**: All required log events generated correctly  
✅ **Telemetry Display**: Enhanced /list command shows poll status  

### Example Logs from Live Test
```
2025-10-05 14:19:16,386 - services.search_service - INFO - {'event': 'poll_start', 'keyword': 'medaille', 'mode': 'rotate', 'pages_to_scan': [1, 8, 9, 10], 'cursor_start': 8, 'window_size': 3}

2025-10-05 14:19:34,566 - services.search_service - INFO - {'event': 'poll_summary', 'keyword': 'medaille', 'mode': 'rotate', 'pages_scanned': 4, 'primary_pages': 1, 'cursor_start': 8, 'window_size': 3, 'unseen_candidates': 0, 'pushed': 0, 'absorbed': 0}
```

## Impact & Benefits

### Problem Resolution
- **No more missed new items** due to end-date sorting
- **Configurable strategy** based on keyword size and requirements  
- **Efficient resource usage** with controlled concurrency and throttling

### Monitoring & Visibility
- **Complete telemetry** for debugging and optimization
- **User-visible status** showing poll mode and progress  
- **Structured logs** for automated monitoring systems

### Scalability
- **Mode selection**: Full-scan for small keywords, rotate for large ones
- **Adaptive limits**: MAX_PAGES_PER_CYCLE prevents runaway crawling
- **Cursor persistence**: Survives bot restarts without losing progress

## Files Modified

1. **`models.py`**: Added poll fields to Keyword model, enhanced SearchResult
2. **`services/search_service.py`**: Complete polling strategy rewrite with deep pagination
3. **`providers/militaria321.py`**: Single-page mode support and enhanced metadata  
4. **`simple_bot.py`**: Enhanced /list command with poll telemetry display
5. **`.env`**: Added deep pagination configuration variables

## Acceptance Criteria Status

✅ **No missed new items**: Polling covers all pages via full or rotating strategy  
✅ **Baseline remains fast**: Unchanged (all pages, no detail fetch, seed only)  
✅ **Posted_ts gating preserved**: Only push if posted_ts >= since_ts or 60min grace  
✅ **Structured logs**: All required events (poll_start, m321_page, poll_summary, decision)  
✅ **Configurable via ENV**: All parameters externally configurable  
✅ **Safe throttling**: 200-600ms jitter, concurrency limits, proper headers  
✅ **Enhanced /list command**: Shows poll mode, pages, cursor, window info  

## Next Steps

1. **Monitor Performance**: Watch logs for page scan efficiency and adjust POLL_WINDOW as needed
2. **Tune Configuration**: Adjust MAX_PAGES_PER_CYCLE based on server response times
3. **Consider Auto-Mode**: Automatically choose full vs rotate based on total_pages_estimate
4. **Add Metrics**: Track average pages scanned per keyword for optimization
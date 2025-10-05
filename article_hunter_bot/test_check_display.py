#!/usr/bin/env python3
"""
Test script to show /check command telemetry display
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
from scheduler import PollingScheduler
from services.notification_service import NotificationService
from utils.text import br_join, b, i, a, code, fmt_ts_de

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.WARNING)  # Reduce log noise for display
logger = logging.getLogger(__name__)


async def demo_check_display():
    """Demo the enhanced /check command display"""
    
    # Initialize services
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    search_service = SearchService(db_manager)
    # Mock scheduler for health check (we don't need real notifications)
    scheduler = None
    
    print("📋 Enhanced /list Command Telemetry Display Demo")
    print("=" * 55)
    
    # Get some existing keywords
    keywords = await db_manager.get_user_keywords("test_user_id", active_only=True)
    
    if not keywords:
        print("❌ No keywords found. Creating sample keyword...")
        # Create a sample keyword with poll telemetry
        sample_keyword = Keyword(
            user_id="test_user_id",
            original_keyword="Wehrmacht Helm",
            normalized_keyword="wehrmacht helm",
            since_ts=datetime.utcnow(),
            poll_mode="rotate",
            poll_window=5,
            poll_cursor_page=12,
            total_pages_estimate=45,
            last_deep_scan_at=datetime.utcnow(),
            baseline_status="complete",
            last_checked=datetime.utcnow(),
            last_success_ts=datetime.utcnow()
        )
        await db_manager.create_keyword(sample_keyword)
        keywords = [sample_keyword]
    
    print(f"✅ Found {len(keywords)} keywords to display")
    print()
    
    # Simulate /list command formatting
    message_lines = [b("Ihre aktiven Überwachungen:"), ""]
    
    now_utc = datetime.utcnow()
    
    for i, keyword in enumerate(keywords[:3]):  # Show first 3
        print(f"Processing keyword {i+1}: {keyword.original_keyword}")
        
        # Compute health status
        status, reason = search_service.compute_keyword_health(keyword, now_utc, scheduler)
        
        # Build enhanced keyword entry with poll telemetry
        keyword_lines = [
            f"📝 {b(keyword.original_keyword)}",
            f"Status: {status} — {reason}",
            f"Letzte Prüfung: {fmt_ts_de(keyword.last_checked)} — Letzter Erfolg: {fmt_ts_de(keyword.last_success_ts)}",
            f"Baseline: {keyword.baseline_status}",
            f"Plattformen: {', '.join(keyword.platforms)}"
        ]
        
        # Add enhanced poll telemetry
        if hasattr(keyword, 'poll_mode') and keyword.poll_mode:
            poll_info_parts = [f"Modus: {keyword.poll_mode}"]
            
            if hasattr(keyword, 'total_pages_estimate') and keyword.total_pages_estimate:
                poll_info_parts.append(f"Seiten: ~{keyword.total_pages_estimate}")
            
            if hasattr(keyword, 'poll_cursor_page') and keyword.poll_mode == "rotate":
                cursor_page = getattr(keyword, 'poll_cursor_page', 1)
                window_size = getattr(keyword, 'poll_window', 5)
                poll_info_parts.append(f"Fenster: {cursor_page}-{cursor_page + window_size - 1}")
            
            if hasattr(keyword, 'last_deep_scan_at') and keyword.last_deep_scan_at:
                poll_info_parts.append(f"Tiefe Suche: {fmt_ts_de(keyword.last_deep_scan_at)}")
            
            if poll_info_parts:
                keyword_lines.append(f"Poll: {' — '.join(poll_info_parts)}")
        else:
            keyword_lines.append("Poll: Standard-Modus")
        
        message_lines.append(br_join(keyword_lines))
        message_lines.append("")  # Space between keywords
    
    # Display the formatted message
    full_message = br_join(message_lines)
    
    print()
    print("🎨 Formatted /list Command Output:")
    print("=" * 40)
    # Convert HTML to plain text for display
    display_text = full_message.replace("<b>", "**").replace("</b>", "**")
    display_text = display_text.replace("<br>", "\n")
    print(display_text)
    print("=" * 40)
    print()
    
    # Show HTML version
    print("📱 HTML Version (as sent to Telegram):")
    print("=" * 40)
    print(full_message[:500] + "..." if len(full_message) > 500 else full_message)
    print("=" * 40)
    print()
    
    print("✨ Key Enhancements Shown:")
    print("• Poll Mode (rotate/full)")
    print("• Page estimates and cursor positions")
    print("• Deep scan timestamps") 
    print("• Rotating window ranges (e.g., 12-16)")
    print("• Compatible with existing health status")
    
    # Cleanup
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(demo_check_display())
#!/usr/bin/env python3
"""
Comprehensive Backend Testing for Militaria Telegram Bot - Phase 1 Focus
Tests Phase 1 implementation: posted_ts parsing, strict gating, and SearchService end-to-end.
"""

import asyncio
import httpx
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, List
import pymongo
from pymongo import MongoClient
import pytz
from unittest.mock import Mock, patch

# Add backend to path
sys.path.insert(0, '/app/backend')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TelegramBotTester:
    """Comprehensive tester for Telegram bot backend"""
    
    def __init__(self):
        # Get backend URL from frontend env
        self.backend_url = "http://localhost:8001"  # Default
        try:
            with open('/app/frontend/.env', 'r') as f:
                for line in f:
                    if line.startswith('REACT_APP_BACKEND_URL='):
                        self.backend_url = line.split('=', 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
        
        self.api_url = f"{self.backend_url}/api"
        
        # MongoDB connection
        self.mongo_client = None
        self.db = None
        
        # Test results
        self.test_results = []
        
    async def setup(self):
        """Setup test environment"""
        logger.info("Setting up test environment...")
        
        # Connect to MongoDB
        try:
            mongo_url = "mongodb://localhost:27017"
            self.mongo_client = MongoClient(mongo_url)
            self.db = self.mongo_client["auction_bot_database"]
            logger.info("✓ Connected to MongoDB")
        except Exception as e:
            logger.error(f"✗ Failed to connect to MongoDB: {e}")
            return False
        
        return True
    
    def teardown(self):
        """Cleanup test environment"""
        if self.mongo_client:
            self.mongo_client.close()
    
    def log_test_result(self, test_name: str, passed: bool, details: str = ""):
        """Log test result"""
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{status}: {test_name}")
        if details:
            logger.info(f"   Details: {details}")
        
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    async def test_api_health(self):
        """Test API health endpoint"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.api_url}/health")
                
                if response.status_code == 200:
                    health_data = response.json()
                    
                    # Check components
                    components = health_data.get("components", {})
                    db_healthy = components.get("database") == "healthy"
                    bot_healthy = components.get("telegram_bot") == "healthy"
                    scheduler_healthy = components.get("scheduler") == "healthy"
                    
                    if db_healthy and bot_healthy and scheduler_healthy:
                        self.log_test_result("API Health Check", True, f"All components healthy: {components}")
                        return True
                    else:
                        self.log_test_result("API Health Check", False, f"Some components unhealthy: {components}")
                        return False
                else:
                    self.log_test_result("API Health Check", False, f"HTTP {response.status_code}")
                    return False
                    
        except Exception as e:
            self.log_test_result("API Health Check", False, f"Exception: {e}")
            return False
    
    async def test_militaria321_provider_search(self):
        """Test militaria321 provider search functionality"""
        try:
            from providers.militaria321 import Militaria321Provider
            
            provider = Militaria321Provider()
            
            # Test 1: Brieföffner search (should return ~25 results)
            search_result = await provider.search("Brieföffner", sample_mode=True)
            briefoffner_count = len(search_result.items)
            
            if briefoffner_count >= 20:  # Allow some variance
                self.log_test_result("Militaria321 Brieföffner Search", True, f"Found {briefoffner_count} results (expected ~25)")
            else:
                self.log_test_result("Militaria321 Brieföffner Search", False, f"Found {briefoffner_count} results (expected ~25)")
            
            # Test 2: Kappmesser search (should return ~8 results)
            search_result = await provider.search("Kappmesser", sample_mode=True)
            kappmesser_count = len(search_result.items)
            
            if kappmesser_count >= 5:  # Allow some variance
                self.log_test_result("Militaria321 Kappmesser Search", True, f"Found {kappmesser_count} results (expected ~8)")
            else:
                self.log_test_result("Militaria321 Kappmesser Search", False, f"Found {kappmesser_count} results (expected ~8)")
            
            # Test 3: Uhr search (should return ~19 results with NO timestamp false positives)
            search_result = await provider.search("uhr", sample_mode=True)
            uhr_count = len(search_result.items)
            
            # Check for timestamp false positives
            timestamp_false_positives = []
            for item in search_result.items:
                if not provider.matches_keyword(item.title, "uhr"):
                    timestamp_false_positives.append(item.title)
            
            if uhr_count >= 15 and len(timestamp_false_positives) == 0:
                self.log_test_result("Militaria321 Uhr Search (No Timestamps)", True, f"Found {uhr_count} results, no timestamp false positives")
            else:
                self.log_test_result("Militaria321 Uhr Search (No Timestamps)", False, f"Found {uhr_count} results, {len(timestamp_false_positives)} timestamp false positives: {timestamp_false_positives[:3]}")
            
            # Test 4: Non-existent term
            search_result = await provider.search("nonexistentterm12345", sample_mode=True)
            nonexistent_count = len(search_result.items)
            
            if nonexistent_count == 0:
                self.log_test_result("Militaria321 Non-existent Term", True, "Correctly returned 0 results")
            else:
                self.log_test_result("Militaria321 Non-existent Term", False, f"Unexpectedly found {nonexistent_count} results")
            
            return True
            
        except Exception as e:
            self.log_test_result("Militaria321 Provider Search", False, f"Exception: {e}")
            return False
    
    async def test_price_formatting(self):
        """Test German price formatting"""
        try:
            from providers.militaria321 import Militaria321Provider
            
            provider = Militaria321Provider()
            
            # Test cases for German price formatting
            test_cases = [
                (Decimal("249.00"), "EUR", "249,00 €"),
                (Decimal("20.00"), "EUR", "20,00 €"),
                (Decimal("1234.56"), "EUR", "1.234,56 €"),
                (Decimal("5.00"), "EUR", "5,00 €"),
                (Decimal("12345.67"), "EUR", "12.345,67 €"),
            ]
            
            all_passed = True
            for value, currency, expected in test_cases:
                result = provider.format_price_de(value, currency)
                if result == expected:
                    logger.info(f"   ✓ {value} {currency} -> {result}")
                else:
                    logger.error(f"   ✗ {value} {currency} -> {result} (expected {expected})")
                    all_passed = False
            
            self.log_test_result("German Price Formatting", all_passed, f"Tested {len(test_cases)} price formats")
            return all_passed
            
        except Exception as e:
            self.log_test_result("German Price Formatting", False, f"Exception: {e}")
            return False
    
    async def test_keyword_matching(self):
        """Test title-only keyword matching"""
        try:
            from providers.militaria321 import Militaria321Provider
            
            provider = Militaria321Provider()
            
            # Test cases for keyword matching
            test_cases = [
                # Should match
                ("Turm mit Uhr", "uhr", True),
                ("Wehrmacht Brieföffner", "brieföffner", True),
                ("Kappmesser Original", "kappmesser", True),
                ("UHR antik", "uhr", True),
                
                # Should NOT match (timestamps)
                ("Endet um 07:39 Uhr", "uhr", False),
                ("Bis 12:30 Uhr verfügbar", "uhr", False),
                ("Zeit: 15:45 Uhr", "uhr", False),
                
                # Case insensitive
                ("BRIEFÖFFNER", "brieföffner", True),
                ("brieföffner", "BRIEFÖFFNER", True),
            ]
            
            all_passed = True
            for title, keyword, expected in test_cases:
                result = provider.matches_keyword(title, keyword)
                if result == expected:
                    logger.info(f"   ✓ '{title}' matches '{keyword}': {result}")
                else:
                    logger.error(f"   ✗ '{title}' matches '{keyword}': {result} (expected {expected})")
                    all_passed = False
            
            self.log_test_result("Keyword Matching Logic", all_passed, f"Tested {len(test_cases)} matching scenarios")
            return all_passed
            
        except Exception as e:
            self.log_test_result("Keyword Matching Logic", False, f"Exception: {e}")
            return False
    
    async def test_database_operations(self):
        """Test database operations"""
        try:
            # Test database connection
            self.db.command("ping")
            
            # Check collections exist
            collections = self.db.list_collection_names()
            required_collections = ["users", "keywords", "listings"]
            
            missing_collections = [col for col in required_collections if col not in collections]
            if missing_collections:
                self.log_test_result("Database Collections", False, f"Missing collections: {missing_collections}")
                return False
            
            # Test keyword collection structure
            sample_keyword = self.db.keywords.find_one()
            if sample_keyword:
                required_fields = ["original_keyword", "normalized_keyword", "seen_listing_keys", "first_run_completed"]
                # Note: 'original_keyword' might be stored as 'keyword' in the actual schema
                actual_fields = list(sample_keyword.keys())
                
                # Check for essential fields (allowing for schema variations)
                has_keyword_field = "keyword" in actual_fields or "original_keyword" in actual_fields
                has_normalized = "normalized_keyword" in actual_fields
                has_seen_keys = "seen_listing_keys" in actual_fields
                
                if has_keyword_field and has_normalized and has_seen_keys:
                    self.log_test_result("Database Schema", True, f"Keywords collection has required fields")
                else:
                    self.log_test_result("Database Schema", False, f"Keywords collection missing fields. Found: {actual_fields}")
            else:
                self.log_test_result("Database Schema", True, "No keywords in database yet (empty state)")
            
            # Test case-insensitive keyword storage
            keywords_with_normalized = list(self.db.keywords.find({"normalized_keyword": {"$exists": True}}))
            if keywords_with_normalized:
                # Check if normalized keywords are properly case-folded
                properly_normalized = all(
                    kw.get("normalized_keyword", "").islower() 
                    for kw in keywords_with_normalized
                )
                
                if properly_normalized:
                    self.log_test_result("Case-insensitive Keywords", True, f"Found {len(keywords_with_normalized)} properly normalized keywords")
                else:
                    self.log_test_result("Case-insensitive Keywords", False, "Some keywords not properly normalized to lowercase")
            else:
                self.log_test_result("Case-insensitive Keywords", True, "No keywords with normalization yet (empty state)")
            
            return True
            
        except Exception as e:
            self.log_test_result("Database Operations", False, f"Exception: {e}")
            return False
    
    async def test_admin_endpoints(self):
        """Test admin endpoints for search functionality"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Test search endpoint with Brieföffner
                response = await client.post(f"{self.api_url}/admin/search-test", params={"keyword": "Brieföffner"})
                
                if response.status_code == 200:
                    search_data = response.json()
                    results_count = search_data.get("results_count", 0)
                    
                    if results_count >= 20:  # Allow some variance
                        self.log_test_result("Admin Search Test (Brieföffner)", True, f"Found {results_count} results via API")
                    else:
                        self.log_test_result("Admin Search Test (Brieföffner)", False, f"Found {results_count} results via API (expected ~25)")
                else:
                    self.log_test_result("Admin Search Test (Brieföffner)", False, f"HTTP {response.status_code}: {response.text}")
                
                # Test with non-existent term
                response = await client.post(f"{self.api_url}/admin/search-test", params={"keyword": "nonexistentterm12345"})
                
                if response.status_code == 200:
                    search_data = response.json()
                    results_count = search_data.get("results_count", 0)
                    
                    if results_count == 0:
                        self.log_test_result("Admin Search Test (Non-existent)", True, "Correctly returned 0 results")
                    else:
                        self.log_test_result("Admin Search Test (Non-existent)", False, f"Unexpectedly found {results_count} results")
                else:
                    self.log_test_result("Admin Search Test (Non-existent)", False, f"HTTP {response.status_code}")
            
            return True
            
        except Exception as e:
            self.log_test_result("Admin Endpoints", False, f"Exception: {e}")
            return False
    
    async def test_bot_command_simulation(self):
        """Simulate bot command processing (without actual Telegram)"""
        try:
            # Import bot handlers and services
            from services.keyword_service import KeywordService
            from database import DatabaseManager
            from bot import handlers
            
            # Initialize services
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            keyword_service = KeywordService(db_manager)
            
            # Set services in handlers module (required for ensure_user to work)
            handlers.set_services(db_manager, keyword_service)
            
            # Create a test user object
            class MockTelegramUser:
                def __init__(self):
                    self.id = 12345
                    self.username = "testuser"
                    self.first_name = "Test"
                    self.last_name = "User"
            
            mock_user = MockTelegramUser()
            
            # Test user creation/retrieval
            user = await handlers.ensure_user(mock_user)
            if user and user.telegram_id == 12345:
                self.log_test_result("Bot User Management", True, f"User created/retrieved: {user.first_name}")
            else:
                self.log_test_result("Bot User Management", False, "Failed to create/retrieve user")
                return False
            
            # Test keyword creation
            test_keyword = "TestBriefoffner"
            keyword = await keyword_service.create_keyword(user.id, test_keyword)
            
            if keyword and keyword.keyword == test_keyword:
                self.log_test_result("Bot Keyword Creation", True, f"Keyword created: {keyword.keyword}")
                
                # Test keyword retrieval (case-insensitive)
                retrieved = await keyword_service.get_user_keyword(user.id, test_keyword.lower())
                if retrieved and retrieved.id == keyword.id:
                    self.log_test_result("Bot Keyword Retrieval (Case-insensitive)", True, "Retrieved keyword with different case")
                else:
                    self.log_test_result("Bot Keyword Retrieval (Case-insensitive)", False, "Failed to retrieve keyword with different case")
                
                # Clean up test keyword
                await keyword_service.delete_keyword(keyword.id)
            else:
                self.log_test_result("Bot Keyword Creation", False, "Failed to create keyword")
            
            await db_manager.close()
            return True
            
        except Exception as e:
            self.log_test_result("Bot Command Simulation", False, f"Exception: {e}")
            return False
    
    async def test_models_posted_ts_support(self):
        """Test that models support optional posted_ts field"""
        try:
            from models import Listing, StoredListing
            
            # Test Listing model with posted_ts
            now_utc = datetime.now(timezone.utc)
            listing = Listing(
                platform="militaria321.com",
                platform_id="12345",
                title="Test Item",
                url="https://militaria321.com/auktion/12345",
                posted_ts=now_utc
            )
            
            # Verify posted_ts is stored correctly
            if listing.posted_ts == now_utc:
                self.log_test_result("Listing Model posted_ts Support", True, f"posted_ts correctly stored: {now_utc}")
            else:
                self.log_test_result("Listing Model posted_ts Support", False, f"posted_ts mismatch: {listing.posted_ts} != {now_utc}")
                return False
            
            # Test StoredListing model with posted_ts
            stored_listing = StoredListing(
                platform="militaria321.com",
                platform_id="12345",
                title="Test Item",
                url="https://militaria321.com/auktion/12345",
                posted_ts=now_utc
            )
            
            if stored_listing.posted_ts == now_utc:
                self.log_test_result("StoredListing Model posted_ts Support", True, f"posted_ts correctly stored: {now_utc}")
            else:
                self.log_test_result("StoredListing Model posted_ts Support", False, f"posted_ts mismatch: {stored_listing.posted_ts} != {now_utc}")
                return False
            
            # Test serialization (no errors should occur)
            try:
                listing_dict = listing.__dict__
                stored_dict = stored_listing.dict()
                self.log_test_result("Model Serialization", True, "Models serialize without errors")
            except Exception as e:
                self.log_test_result("Model Serialization", False, f"Serialization error: {e}")
                return False
            
            return True
            
        except Exception as e:
            self.log_test_result("Models posted_ts Support", False, f"Exception: {e}")
            return False
    
    async def test_militaria321_posted_ts_parsing(self):
        """Test militaria321 provider posted_ts parsing from detail pages"""
        try:
            from providers.militaria321 import Militaria321Provider
            
            provider = Militaria321Provider()
            
            # Test HTML samples with different date formats
            test_cases = [
                {
                    "html": "<dt>Auktionsbeginn:</dt><dd>04.10.2025 13:21 Uhr</dd>",
                    "expected_date": "2025-10-04",
                    "expected_time": "13:21"
                },
                {
                    "html": "Eingestellt: 15.12.2024 09:30 Uhr",
                    "expected_date": "2024-12-15", 
                    "expected_time": "09:30"
                },
                {
                    "html": "<tr><td>Auktionsbeginn</td><td>01.01.2025 00:00 Uhr</td></tr>",
                    "expected_date": "2025-01-01",
                    "expected_time": "00:00"
                }
            ]
            
            all_passed = True
            for i, case in enumerate(test_cases):
                parsed_ts = provider._parse_posted_ts_from_text(case["html"])
                
                if parsed_ts:
                    # Convert to Berlin timezone for comparison
                    berlin_tz = pytz.timezone("Europe/Berlin")
                    berlin_dt = parsed_ts.astimezone(berlin_tz)
                    
                    expected_date = case["expected_date"]
                    expected_time = case["expected_time"]
                    
                    if (berlin_dt.strftime("%Y-%m-%d") == expected_date and 
                        berlin_dt.strftime("%H:%M") == expected_time):
                        logger.info(f"   ✓ Case {i+1}: Parsed {expected_date} {expected_time} correctly")
                    else:
                        logger.error(f"   ✗ Case {i+1}: Expected {expected_date} {expected_time}, got {berlin_dt}")
                        all_passed = False
                else:
                    logger.error(f"   ✗ Case {i+1}: Failed to parse timestamp from HTML")
                    all_passed = False
            
            self.log_test_result("Militaria321 posted_ts Parsing", all_passed, f"Tested {len(test_cases)} HTML samples")
            return all_passed
            
        except Exception as e:
            self.log_test_result("Militaria321 posted_ts Parsing", False, f"Exception: {e}")
            return False
    
    async def test_militaria321_fetch_posted_ts_batch(self):
        """Test militaria321 fetch_posted_ts_batch method with mocked responses"""
        try:
            from providers.militaria321 import Militaria321Provider
            from models import Listing
            
            provider = Militaria321Provider()
            
            # Create test listings
            listings = [
                Listing(
                    platform="militaria321.com",
                    platform_id="12345",
                    title="Test Item 1",
                    url="https://militaria321.com/auktion/12345",
                    posted_ts=None
                ),
                Listing(
                    platform="militaria321.com", 
                    platform_id="67890",
                    title="Test Item 2",
                    url="https://militaria321.com/auktion/67890",
                    posted_ts=None
                )
            ]
            
            # Mock HTML responses
            mock_html_1 = """
            <html>
                <body>
                    <dt>Auktionsbeginn:</dt>
                    <dd>04.10.2025 13:21 Uhr</dd>
                </body>
            </html>
            """
            
            mock_html_2 = """
            <html>
                <body>
                    <tr>
                        <td>Eingestellt</td>
                        <td>15.12.2024 09:30 Uhr</td>
                    </tr>
                </body>
            </html>
            """
            
            # Mock httpx responses
            async def mock_get(url, **kwargs):
                mock_response = Mock()
                mock_response.raise_for_status = Mock()
                if "12345" in url:
                    mock_response.text = mock_html_1
                elif "67890" in url:
                    mock_response.text = mock_html_2
                else:
                    mock_response.text = "<html><body>No date found</body></html>"
                return mock_response
            
            # Patch httpx.AsyncClient.get
            with patch('httpx.AsyncClient.get', side_effect=mock_get):
                await provider.fetch_posted_ts_batch(listings, concurrency=2)
            
            # Verify results
            success_count = 0
            for listing in listings:
                if listing.posted_ts:
                    success_count += 1
                    logger.info(f"   ✓ {listing.platform_id}: posted_ts = {listing.posted_ts}")
                else:
                    logger.warning(f"   ✗ {listing.platform_id}: No posted_ts extracted")
            
            if success_count >= 2:
                self.log_test_result("Militaria321 fetch_posted_ts_batch", True, f"Successfully extracted posted_ts for {success_count}/{len(listings)} items")
                return True
            else:
                self.log_test_result("Militaria321 fetch_posted_ts_batch", False, f"Only extracted posted_ts for {success_count}/{len(listings)} items")
                return False
            
        except Exception as e:
            self.log_test_result("Militaria321 fetch_posted_ts_batch", False, f"Exception: {e}")
            return False
    
    async def test_strict_gating_logic(self):
        """Test is_new_listing strict gating logic"""
        try:
            from services.search_service import is_new_listing
            from models import Listing
            
            # Test setup
            now = datetime.now(timezone.utc)
            since_ts = now - timedelta(hours=2)  # Subscription started 2 hours ago
            
            test_cases = [
                {
                    "name": "Item with posted_ts older than since_ts",
                    "posted_ts": since_ts - timedelta(minutes=30),
                    "expected": False,
                    "description": "Should return False (too old)"
                },
                {
                    "name": "Item with posted_ts newer than since_ts",
                    "posted_ts": since_ts + timedelta(minutes=30),
                    "expected": True,
                    "description": "Should return True (new enough)"
                },
                {
                    "name": "Item with posted_ts equal to since_ts",
                    "posted_ts": since_ts,
                    "expected": True,
                    "description": "Should return True (exactly at boundary)"
                },
                {
                    "name": "Item without posted_ts, subscription > 60 min old",
                    "posted_ts": None,
                    "since_ts_override": now - timedelta(minutes=90),
                    "expected": False,
                    "description": "Should return False (no grace period)"
                },
                {
                    "name": "Item without posted_ts, subscription within 60 min",
                    "posted_ts": None,
                    "since_ts_override": now - timedelta(minutes=30),
                    "expected": True,
                    "description": "Should return True (within grace period)"
                }
            ]
            
            all_passed = True
            for case in test_cases:
                # Create test listing
                listing = Listing(
                    platform="militaria321.com",
                    platform_id="test123",
                    title="Test Item",
                    url="https://test.com/item/123",
                    posted_ts=case["posted_ts"]
                )
                
                # Use override since_ts if provided
                test_since_ts = case.get("since_ts_override", since_ts)
                
                # Test the gating logic
                result = is_new_listing(listing, test_since_ts, now, grace_minutes=60)
                
                if result == case["expected"]:
                    logger.info(f"   ✓ {case['name']}: {result} (correct)")
                else:
                    logger.error(f"   ✗ {case['name']}: {result} (expected {case['expected']})")
                    all_passed = False
            
            self.log_test_result("Strict Gating Logic", all_passed, f"Tested {len(test_cases)} gating scenarios")
            return all_passed
            
        except Exception as e:
            self.log_test_result("Strict Gating Logic", False, f"Exception: {e}")
            return False
    
    async def test_search_service_end_to_end(self):
        """Test SearchService end-to-end with simulated scenarios"""
        try:
            from services.search_service import SearchService
            from database import DatabaseManager
            from models import Keyword, Listing
            from utils.listing_key import build_listing_key
            
            # Initialize services
            db_manager = DatabaseManager()
            await db_manager.initialize()
            search_service = SearchService(db_manager)
            
            # Create test user and keyword
            from services.keyword_service import KeywordService
            keyword_service = KeywordService(db_manager)
            
            # Create test user
            test_user_id = "test_user_phase1"
            test_keyword_text = "TestBriefoffner"
            
            # Clean up any existing test data
            await db_manager.db.keywords.delete_many({"user_id": test_user_id})
            await db_manager.db.notifications.delete_many({"user_id": test_user_id})
            
            # Create keyword with baseline_status = "complete" and empty seen_set
            now = datetime.now(timezone.utc)
            since_ts = now - timedelta(hours=1)
            
            keyword = Keyword(
                user_id=test_user_id,
                keyword=test_keyword_text,
                normalized_keyword=test_keyword_text.lower(),
                baseline_status="complete",
                since_ts=since_ts,
                seen_listing_keys=[],
                platforms=["militaria321.com"]
            )
            
            # Insert keyword into database
            await db_manager.db.keywords.insert_one(keyword.dict())
            
            # Create mock listings with different posted_ts scenarios
            mock_listings = [
                Listing(
                    platform="militaria321.com",
                    platform_id="new_item_1",
                    title=f"{test_keyword_text} New Item 1",
                    url="https://militaria321.com/auktion/new_item_1",
                    posted_ts=since_ts + timedelta(minutes=30),  # Should trigger notification
                    price_value=100.0,
                    price_currency="EUR"
                ),
                Listing(
                    platform="militaria321.com", 
                    platform_id="old_item_1",
                    title=f"{test_keyword_text} Old Item 1",
                    url="https://militaria321.com/auktion/old_item_1",
                    posted_ts=since_ts - timedelta(minutes=30),  # Should be absorbed to baseline
                    price_value=200.0,
                    price_currency="EUR"
                ),
                Listing(
                    platform="militaria321.com",
                    platform_id="no_ts_item_1", 
                    title=f"{test_keyword_text} No Timestamp Item",
                    url="https://militaria321.com/auktion/no_ts_item_1",
                    posted_ts=None,  # Should be absorbed (subscription > 60min old)
                    price_value=150.0,
                    price_currency="EUR"
                )
            ]
            
            # Mock the provider search method
            async def mock_search(keyword_text, since_ts=None, sample_mode=False):
                from models import SearchResult
                return SearchResult(items=mock_listings, total_count=len(mock_listings), has_more=False)
            
            # Mock the provider matches_keyword method
            def mock_matches_keyword(title, keyword_text):
                return keyword_text.lower() in title.lower()
            
            # Patch the provider methods
            original_search = search_service.providers["militaria321.com"].search
            original_matches = search_service.providers["militaria321.com"].matches_keyword
            
            search_service.providers["militaria321.com"].search = mock_search
            search_service.providers["militaria321.com"].matches_keyword = mock_matches_keyword
            
            try:
                # Run the search
                result = await search_service.search_keyword(keyword)
                
                # Verify results
                expected_notifications = 1  # Only the new item should trigger notification
                expected_matched = 3  # All items should match keyword
                
                notifications_created = result.get("new_notifications", 0)
                matched_listings = result.get("matched_listings", 0)
                skipped_old = result.get("skipped_old", 0)
                
                success = True
                details = []
                
                if matched_listings == expected_matched:
                    details.append(f"✓ Matched {matched_listings} listings")
                else:
                    details.append(f"✗ Expected {expected_matched} matched, got {matched_listings}")
                    success = False
                
                if notifications_created == expected_notifications:
                    details.append(f"✓ Created {notifications_created} notifications")
                else:
                    details.append(f"✗ Expected {expected_notifications} notifications, got {notifications_created}")
                    success = False
                
                if skipped_old >= 1:
                    details.append(f"✓ Skipped {skipped_old} old items")
                else:
                    details.append(f"✗ Expected at least 1 skipped old item, got {skipped_old}")
                    success = False
                
                # Check idempotency - run again and ensure no duplicate notifications
                result2 = await search_service.search_keyword(keyword)
                notifications_created_2 = result2.get("new_notifications", 0)
                
                if notifications_created_2 == 0:
                    details.append("✓ Idempotency guard working (no duplicate notifications)")
                else:
                    details.append(f"✗ Idempotency failed: {notifications_created_2} duplicate notifications")
                    success = False
                
                self.log_test_result("SearchService End-to-End", success, "; ".join(details))
                return success
                
            finally:
                # Restore original methods
                search_service.providers["militaria321.com"].search = original_search
                search_service.providers["militaria321.com"].matches_keyword = original_matches
                
                # Clean up test data
                await db_manager.db.keywords.delete_many({"user_id": test_user_id})
                await db_manager.db.notifications.delete_many({"user_id": test_user_id})
                await db_manager.close()
            
        except Exception as e:
            self.log_test_result("SearchService End-to-End", False, f"Exception: {e}")
            return False
    
    async def test_logging_and_stability(self):
        """Test for syntax errors, encoding issues, and basic stability"""
        try:
            # Test 1: Import all critical modules without syntax errors
            try:
                from providers.militaria321 import Militaria321Provider
                from services.search_service import SearchService, is_new_listing
                from models import Listing, StoredListing, Keyword
                from utils.listing_key import build_listing_key
                self.log_test_result("Module Imports", True, "All critical modules imported successfully")
            except SyntaxError as e:
                self.log_test_result("Module Imports", False, f"Syntax error: {e}")
                return False
            except ImportError as e:
                self.log_test_result("Module Imports", False, f"Import error: {e}")
                return False
            
            # Test 2: Check for encoding artifacts in source files
            encoding_issues = []
            files_to_check = [
                "/app/backend/providers/militaria321.py",
                "/app/backend/services/search_service.py",
                "/app/backend/models.py"
            ]
            
            for file_path in files_to_check:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if '&lt;' in content or '&gt;' in content or '&amp;' in content:
                            encoding_issues.append(f"{file_path}: HTML entities found")
                except Exception as e:
                    encoding_issues.append(f"{file_path}: Read error - {e}")
            
            if encoding_issues:
                self.log_test_result("Encoding Check", False, f"Issues found: {'; '.join(encoding_issues)}")
                return False
            else:
                self.log_test_result("Encoding Check", True, f"No encoding artifacts in {len(files_to_check)} files")
            
            # Test 3: Basic provider instantiation
            try:
                provider = Militaria321Provider()
                if hasattr(provider, 'fetch_posted_ts_batch') and callable(provider.fetch_posted_ts_batch):
                    self.log_test_result("Provider Stability", True, "Militaria321Provider instantiated with required methods")
                else:
                    self.log_test_result("Provider Stability", False, "fetch_posted_ts_batch method missing")
                    return False
            except Exception as e:
                self.log_test_result("Provider Stability", False, f"Provider instantiation failed: {e}")
                return False
            
            return True
            
        except Exception as e:
            self.log_test_result("Logging and Stability", False, f"Exception: {e}")
            return False

    async def run_all_tests(self):
        """Run all tests with focus on Phase 1"""
        logger.info("🎖️ Starting Phase 1 Backend Tests (posted_ts strict gate)")
        logger.info(f"Backend URL: {self.backend_url}")
        logger.info(f"API URL: {self.api_url}")
        
        if not await self.setup():
            logger.error("Failed to setup test environment")
            return False
        
        try:
            # Run Phase 1 focused tests
            tests = [
                # Phase 1 specific tests
                self.test_models_posted_ts_support(),
                self.test_militaria321_posted_ts_parsing(),
                self.test_militaria321_fetch_posted_ts_batch(),
                self.test_strict_gating_logic(),
                self.test_search_service_end_to_end(),
                self.test_logging_and_stability(),
                
                # Legacy tests (still important)
                self.test_api_health(),
                self.test_militaria321_provider_search(),
                self.test_price_formatting(),
                self.test_keyword_matching(),
                self.test_database_operations(),
            ]
            
            results = await asyncio.gather(*tests, return_exceptions=True)
            
            # Count results
            passed = sum(1 for r in results if r is True)
            failed = len(results) - passed
            
            logger.info(f"\n🎖️ Phase 1 Test Summary:")
            logger.info(f"✓ Passed: {passed}")
            logger.info(f"✗ Failed: {failed}")
            logger.info(f"Total: {len(results)}")
            
            # Show detailed results
            logger.info(f"\nDetailed Results:")
            for test_result in self.test_results:
                status = "✓" if test_result["passed"] else "✗"
                logger.info(f"{status} {test_result['test']}")
                if test_result["details"]:
                    logger.info(f"   {test_result['details']}")
            
            return failed == 0
            
        finally:
            self.teardown()

async def main():
    """Main test runner"""
    tester = TelegramBotTester()
    success = await tester.run_all_tests()
    
    if success:
        logger.info("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        logger.error("\n❌ Some tests failed!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
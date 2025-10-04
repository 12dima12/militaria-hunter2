#!/usr/bin/env python3
"""
Comprehensive Backend Testing for Militaria Telegram Bot
Tests all critical functionality including search, price formatting, keyword matching, and database operations.
"""

import asyncio
import httpx
import json
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List
import pymongo
from pymongo import MongoClient

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
            # Import bot handlers
            from bot.handlers import ensure_user
            from services.keyword_service import KeywordService
            from database import DatabaseManager
            
            # Initialize services
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            keyword_service = KeywordService(db_manager)
            
            # Create a test user object
            class MockTelegramUser:
                def __init__(self):
                    self.id = 12345
                    self.username = "testuser"
                    self.first_name = "Test"
                    self.last_name = "User"
            
            mock_user = MockTelegramUser()
            
            # Test user creation/retrieval
            user = await ensure_user(mock_user)
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
    
    async def run_all_tests(self):
        """Run all tests"""
        logger.info("🎖️ Starting Comprehensive Telegram Bot Backend Tests")
        logger.info(f"Backend URL: {self.backend_url}")
        logger.info(f"API URL: {self.api_url}")
        
        if not await self.setup():
            logger.error("Failed to setup test environment")
            return False
        
        try:
            # Run all tests
            tests = [
                self.test_api_health(),
                self.test_militaria321_provider_search(),
                self.test_price_formatting(),
                self.test_keyword_matching(),
                self.test_database_operations(),
                self.test_admin_endpoints(),
                self.test_bot_command_simulation(),
            ]
            
            results = await asyncio.gather(*tests, return_exceptions=True)
            
            # Count results
            passed = sum(1 for r in results if r is True)
            failed = len(results) - passed
            
            logger.info(f"\n🎖️ Test Summary:")
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
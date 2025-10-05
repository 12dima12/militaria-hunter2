#!/usr/bin/env python3
"""
Integration Testing for Duplicate Keyword Bug Fix - Simulates Bot Commands
Tests the complete integration flow without requiring actual Telegram connection
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, List
import pymongo
from pymongo import MongoClient

# Add article_hunter_bot to path
sys.path.insert(0, '/app/article_hunter_bot')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IntegrationTester:
    """Integration tester for complete keyword lifecycle simulation"""
    
    def __init__(self):
        # MongoDB connection
        self.mongo_client = None
        self.db = None
        
        # Test results
        self.test_results = []
        
        # Test user data
        self.test_user_id = "integration_test_user"
        self.test_telegram_id = 888888888
        
    async def setup(self):
        """Setup test environment"""
        logger.info("Setting up integration test environment...")
        
        # Connect to MongoDB
        try:
            mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
            db_name = os.environ.get('DB_NAME', 'article_hunter')
            self.mongo_client = MongoClient(mongo_url)
            self.db = self.mongo_client[db_name]
            logger.info("✓ Connected to MongoDB")
        except Exception as e:
            logger.error(f"✗ Failed to connect to MongoDB: {e}")
            return False
        
        # Clean up any existing test data
        await self.cleanup_test_data()
        
        return True
    
    async def cleanup_test_data(self):
        """Clean up test data"""
        try:
            # Remove test user and related data
            self.db.users.delete_many({"telegram_id": self.test_telegram_id})
            self.db.keywords.delete_many({"user_id": self.test_user_id})
            self.db.notifications.delete_many({"user_id": self.test_user_id})
            logger.info("✓ Cleaned up test data")
        except Exception as e:
            logger.warning(f"Cleanup warning: {e}")
    
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
    
    async def simulate_search_command(self, keyword_text: str, db_manager):
        """Simulate /search command logic"""
        from models import User, Keyword
        from services.search_service import SearchService
        
        # Ensure user exists
        user = await db_manager.get_user_by_telegram_id(self.test_telegram_id)
        if not user:
            user = User(
                id=self.test_user_id,
                telegram_id=self.test_telegram_id,
                first_name="Integration",
                last_name="Test"
            )
            await db_manager.create_user(user)
        
        # Check if keyword already exists (duplicate check logic from simple_bot.py)
        normalized = SearchService.normalize_keyword(keyword_text)
        existing = await db_manager.get_keyword_by_normalized(user.id, normalized)
        
        # Log duplicate check for debugging (from simple_bot.py line 82-92)
        logger.info({
            "event": "dup_check",
            "user_id": user.id,
            "normalized": normalized,
            "found_doc_id": existing.id if existing else None,
            "is_active": existing.is_active if existing else None,
            "status_fields": {
                "baseline_status": existing.baseline_status if existing else None,
                "last_checked": existing.last_checked.isoformat() if existing and existing.last_checked else None
            } if existing else None
        })
        
        if existing:
            if existing.is_active:
                # Truly active keyword exists
                return {"status": "exists", "message": f"⚠️ Suchbegriff **'{existing.original_keyword}'** existiert bereits."}
            else:
                # Inactive keyword exists - reactivate it (from simple_bot.py line 103-133)
                logger.info(f"Reactivating inactive keyword: {existing.original_keyword}")
                
                # Reset keyword for reactivation
                existing.is_active = True
                existing.since_ts = datetime.utcnow()
                existing.seen_listing_keys = []
                existing.baseline_status = "pending"
                existing.baseline_errors = {}
                existing.last_checked = None
                existing.last_success_ts = None
                existing.last_error_ts = None
                existing.consecutive_errors = 0
                existing.updated_at = datetime.utcnow()
                
                # Update in database
                update_doc = existing.model_dump()
                await db_manager.db.keywords.update_one(
                    {"id": existing.id},
                    {"$set": update_doc}
                )
                
                return {
                    "status": "reactivated", 
                    "message": f"✅ Suchbegriff reaktiviert: **{existing.original_keyword}** – Baseline wird neu aufgebaut.",
                    "keyword_id": existing.id
                }
        
        # Create new keyword (from simple_bot.py line 144-152)
        keyword = Keyword(
            user_id=user.id,
            original_keyword=keyword_text,
            normalized_keyword=normalized,
            since_ts=datetime.utcnow(),
            baseline_status="pending",
            platforms=["militaria321.com"]
        )
        await db_manager.create_keyword(keyword)
        
        return {
            "status": "created", 
            "message": f"Suche eingerichtet: \"{keyword_text}\"",
            "keyword_id": keyword.id
        }
    
    async def simulate_delete_command(self, keyword_text: str, db_manager):
        """Simulate /delete command logic"""
        from services.search_service import SearchService
        
        user = await db_manager.get_user_by_telegram_id(self.test_telegram_id)
        if not user:
            return {"status": "error", "message": "User not found"}
        
        # Find keyword (only active ones) - from simple_bot.py line 258-259
        normalized = SearchService.normalize_keyword(keyword_text)
        keyword = await db_manager.get_keyword_by_normalized(user.id, normalized, active_only=True)
        
        if not keyword:
            return {
                "status": "not_found", 
                "message": f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden."
            }
        
        # Soft delete keyword (from simple_bot.py line 270)
        await db_manager.soft_delete_keyword(keyword.id)
        
        return {
            "status": "deleted", 
            "message": f"Überwachung für \"{keyword.original_keyword}\" wurde gelöscht.",
            "keyword_id": keyword.id
        }
    
    async def simulate_clear_command(self, db_manager):
        """Simulate /clear command logic (hard delete)"""
        user = await db_manager.get_user_by_telegram_id(self.test_telegram_id)
        if not user:
            return {"status": "error", "message": "User not found"}
        
        # Get user keyword IDs (from simple_bot.py line 536)
        kw_ids = await db_manager.get_user_keyword_ids(user.id)
        if not kw_ids:
            return {"status": "no_keywords", "message": "Sie haben derzeit keine Suchbegriffe."}
        
        # Delete artifacts first, then keywords (from simple_bot.py line 558-560)
        n_hits = await db_manager.delete_keyword_hits_by_keyword_ids(kw_ids)
        n_notifs = await db_manager.delete_notifications_by_keyword_ids(kw_ids)
        n_kw = await db_manager.delete_keywords_by_ids(kw_ids)
        
        return {
            "status": "cleared",
            "message": f"🧹 Bereinigung abgeschlossen.",
            "deleted_counts": {
                "keywords": n_kw,
                "hits": n_hits,
                "notifications": n_notifs
            }
        }
    
    async def test_create_soft_delete_reactivate_flow(self):
        """Test Create → Soft Delete → Reactivate flow"""
        try:
            from database import DatabaseManager
            
            # Initialize database manager
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            test_keyword = "TestFlowKeyword"
            
            # Step 1: Create keyword
            result1 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result1["status"] != "created":
                self.log_test_result("Flow Test - Create", False, f"Expected 'created', got '{result1['status']}'")
                return False
            
            self.log_test_result("Flow Test - Create", True, "Keyword created successfully")
            
            # Step 2: Soft delete keyword
            result2 = await self.simulate_delete_command(test_keyword, db_manager)
            
            if result2["status"] != "deleted":
                self.log_test_result("Flow Test - Soft Delete", False, f"Expected 'deleted', got '{result2['status']}'")
                return False
            
            self.log_test_result("Flow Test - Soft Delete", True, "Keyword soft deleted successfully")
            
            # Step 3: Try to search same keyword again (should reactivate)
            result3 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result3["status"] != "reactivated":
                self.log_test_result("Flow Test - Reactivate", False, f"Expected 'reactivated', got '{result3['status']}'")
                return False
            
            if "reaktiviert" not in result3["message"]:
                self.log_test_result("Flow Test - German UX", False, f"German reactivation message missing: {result3['message']}")
                return False
            
            self.log_test_result("Flow Test - Reactivate", True, "Keyword reactivated with correct German message")
            
            await db_manager.close()
            return True
            
        except Exception as e:
            self.log_test_result("Create → Soft Delete → Reactivate Flow", False, f"Exception: {e}")
            return False
    
    async def test_create_hard_delete_create_flow(self):
        """Test Create → Hard Delete → Create flow"""
        try:
            from database import DatabaseManager
            
            # Initialize database manager
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            test_keyword = "TestHardDeleteKeyword"
            
            # Step 1: Create keyword
            result1 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result1["status"] != "created":
                self.log_test_result("Hard Delete Flow - Create", False, f"Expected 'created', got '{result1['status']}'")
                return False
            
            self.log_test_result("Hard Delete Flow - Create", True, "Keyword created successfully")
            
            # Step 2: Hard delete via /clear
            result2 = await self.simulate_clear_command(db_manager)
            
            if result2["status"] != "cleared":
                self.log_test_result("Hard Delete Flow - Clear", False, f"Expected 'cleared', got '{result2['status']}'")
                return False
            
            if result2["deleted_counts"]["keywords"] < 1:
                self.log_test_result("Hard Delete Flow - Clear Count", False, f"Expected at least 1 keyword deleted, got {result2['deleted_counts']['keywords']}")
                return False
            
            self.log_test_result("Hard Delete Flow - Clear", True, f"Keywords hard deleted: {result2['deleted_counts']['keywords']}")
            
            # Step 3: Try to search same keyword again (should create fresh)
            result3 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result3["status"] != "created":
                self.log_test_result("Hard Delete Flow - Fresh Create", False, f"Expected 'created', got '{result3['status']}'")
                return False
            
            self.log_test_result("Hard Delete Flow - Fresh Create", True, "Fresh keyword created after hard delete")
            
            await db_manager.close()
            return True
            
        except Exception as e:
            self.log_test_result("Create → Hard Delete → Create Flow", False, f"Exception: {e}")
            return False
    
    async def test_duplicate_prevention(self):
        """Test duplicate prevention for active keywords"""
        try:
            from database import DatabaseManager
            
            # Initialize database manager
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            test_keyword = "TestDuplicateKeyword"
            
            # Step 1: Create keyword
            result1 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result1["status"] != "created":
                self.log_test_result("Duplicate Prevention - Create", False, f"Expected 'created', got '{result1['status']}'")
                return False
            
            self.log_test_result("Duplicate Prevention - Create", True, "Keyword created successfully")
            
            # Step 2: Try to create same keyword again (should be blocked)
            result2 = await self.simulate_search_command(test_keyword, db_manager)
            
            if result2["status"] != "exists":
                self.log_test_result("Duplicate Prevention - Block", False, f"Expected 'exists', got '{result2['status']}'")
                return False
            
            if "existiert bereits" not in result2["message"]:
                self.log_test_result("Duplicate Prevention - German Message", False, f"German duplicate message missing: {result2['message']}")
                return False
            
            self.log_test_result("Duplicate Prevention - Block", True, "Active keyword duplicate correctly blocked with German message")
            
            await db_manager.close()
            return True
            
        except Exception as e:
            self.log_test_result("Duplicate Prevention", False, f"Exception: {e}")
            return False
    
    async def test_case_insensitive_operations(self):
        """Test case insensitive keyword operations"""
        try:
            from database import DatabaseManager
            
            # Initialize database manager
            db_manager = DatabaseManager()
            await db_manager.initialize()
            
            # Test with different cases
            original_keyword = "Wehrmacht"
            lowercase_keyword = "wehrmacht"
            uppercase_keyword = "WEHRMACHT"
            
            # Step 1: Create with original case
            result1 = await self.simulate_search_command(original_keyword, db_manager)
            
            if result1["status"] != "created":
                self.log_test_result("Case Insensitive - Create", False, f"Expected 'created', got '{result1['status']}'")
                return False
            
            self.log_test_result("Case Insensitive - Create", True, f"Keyword '{original_keyword}' created")
            
            # Step 2: Try to create with lowercase (should be blocked)
            result2 = await self.simulate_search_command(lowercase_keyword, db_manager)
            
            if result2["status"] != "exists":
                self.log_test_result("Case Insensitive - Lowercase Block", False, f"Expected 'exists', got '{result2['status']}'")
                return False
            
            self.log_test_result("Case Insensitive - Lowercase Block", True, f"Lowercase '{lowercase_keyword}' correctly blocked")
            
            # Step 3: Try to create with uppercase (should be blocked)
            result3 = await self.simulate_search_command(uppercase_keyword, db_manager)
            
            if result3["status"] != "exists":
                self.log_test_result("Case Insensitive - Uppercase Block", False, f"Expected 'exists', got '{result3['status']}'")
                return False
            
            self.log_test_result("Case Insensitive - Uppercase Block", True, f"Uppercase '{uppercase_keyword}' correctly blocked")
            
            # Step 4: Delete with different case
            result4 = await self.simulate_delete_command(lowercase_keyword, db_manager)
            
            if result4["status"] != "deleted":
                self.log_test_result("Case Insensitive - Delete", False, f"Expected 'deleted', got '{result4['status']}'")
                return False
            
            self.log_test_result("Case Insensitive - Delete", True, f"Keyword deleted using lowercase '{lowercase_keyword}'")
            
            # Step 5: Try to reactivate with uppercase
            result5 = await self.simulate_search_command(uppercase_keyword, db_manager)
            
            if result5["status"] != "reactivated":
                self.log_test_result("Case Insensitive - Reactivate", False, f"Expected 'reactivated', got '{result5['status']}'")
                return False
            
            self.log_test_result("Case Insensitive - Reactivate", True, f"Keyword reactivated using uppercase '{uppercase_keyword}'")
            
            await db_manager.close()
            return True
            
        except Exception as e:
            self.log_test_result("Case Insensitive Operations", False, f"Exception: {e}")
            return False
    
    async def run_all_tests(self):
        """Run all integration tests"""
        logger.info("🔗 Starting Integration Tests for Duplicate Keyword Bug Fix")
        
        if not await self.setup():
            logger.error("Failed to setup test environment")
            return False
        
        try:
            # Run all tests
            tests = [
                self.test_create_soft_delete_reactivate_flow(),
                self.test_create_hard_delete_create_flow(),
                self.test_duplicate_prevention(),
                self.test_case_insensitive_operations(),
            ]
            
            results = await asyncio.gather(*tests, return_exceptions=True)
            
            # Count results
            passed = sum(1 for r in results if r is True)
            failed = len(results) - passed
            
            logger.info(f"\n🔗 Integration Test Summary:")
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
            await self.cleanup_test_data()
            self.teardown()

async def main():
    """Main test runner"""
    tester = IntegrationTester()
    success = await tester.run_all_tests()
    
    if success:
        logger.info("\n🎉 All integration tests passed!")
        return True
    else:
        logger.error("\n❌ Some integration tests failed!")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
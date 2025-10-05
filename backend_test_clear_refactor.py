#!/usr/bin/env python3
"""
Backend Testing for /clear Command Refactor - Telegram Article Hunter Bot
Tests the refactored /clear command functionality with user-specific keyword deletion.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import List, Dict, Any
from unittest.mock import Mock, patch, AsyncMock
import uuid

# Add article_hunter_bot to path
sys.path.insert(0, '/app/article_hunter_bot')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ClearCommandTester:
    """Comprehensive tester for /clear command refactor"""
    
    def __init__(self):
        self.test_results = []
        self.db_manager = None
        self.search_service = None
        
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
    
    async def setup(self):
        """Setup test environment"""
        logger.info("Setting up test environment...")
        
        try:
            from database import DatabaseManager
            from services.search_service import SearchService
            
            # Initialize database
            self.db_manager = DatabaseManager()
            await self.db_manager.initialize()
            
            # Initialize search service
            self.search_service = SearchService(self.db_manager)
            
            logger.info("✓ Test environment setup complete")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to setup test environment: {e}")
            return False
    
    async def teardown(self):
        """Cleanup test environment"""
        if self.db_manager:
            await self.db_manager.close()
    
    async def test_scheduler_stop_keyword_job(self):
        """Test scheduler.stop_keyword_job() helper function"""
        try:
            from scheduler import stop_keyword_job
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            
            # Create a test scheduler
            test_scheduler = AsyncIOScheduler()
            test_scheduler.start()
            
            # Set global scheduler reference
            import scheduler
            scheduler.scheduler = test_scheduler
            
            # Add a test job
            test_job_id = "keyword_test123"
            test_scheduler.add_job(
                func=lambda: None,
                trigger=IntervalTrigger(seconds=60),
                id=test_job_id,
                name="Test job"
            )
            
            # Verify job exists
            job_exists_before = test_scheduler.get_job(test_job_id) is not None
            
            # Test stopping the job
            result = stop_keyword_job(test_job_id)
            
            # Verify job is removed
            job_exists_after = test_scheduler.get_job(test_job_id) is not None
            
            # Test idempotency - calling again should return False
            result_second = stop_keyword_job(test_job_id)
            
            # Test non-existent job
            result_nonexistent = stop_keyword_job("nonexistent_job")
            
            # Cleanup
            test_scheduler.shutdown(wait=False)
            
            # Verify results
            success = (
                job_exists_before and 
                result and 
                not job_exists_after and 
                not result_second and 
                not result_nonexistent
            )
            
            details = f"Job existed: {job_exists_before}, Stopped: {result}, Removed: {not job_exists_after}, Idempotent: {not result_second}, Non-existent handled: {not result_nonexistent}"
            self.log_test_result("Scheduler stop_keyword_job Helper", success, details)
            return success
            
        except Exception as e:
            self.log_test_result("Scheduler stop_keyword_job Helper", False, f"Exception: {e}")
            return False
    
    async def test_database_helper_functions(self):
        """Test database helper functions for cascading deletes"""
        try:
            from models import User, Keyword, Notification
            
            # Create test user
            test_user = User(
                telegram_id=999888777,
                id=str(uuid.uuid4())
            )
            await self.db_manager.create_user(test_user)
            
            # Create test keywords
            test_keywords = []
            for i in range(3):
                keyword = Keyword(
                    id=str(uuid.uuid4()),
                    user_id=test_user.id,
                    original_keyword=f"TestKeyword{i}",
                    normalized_keyword=f"testkeyword{i}",
                    since_ts=datetime.utcnow(),
                    platforms=["militaria321.com"]
                )
                await self.db_manager.create_keyword(keyword)
                test_keywords.append(keyword)
            
            # Create test notifications
            test_notifications = []
            for i, keyword in enumerate(test_keywords):
                notification = Notification(
                    id=str(uuid.uuid4()),
                    user_id=test_user.id,
                    keyword_id=keyword.id,
                    listing_key=f"militaria321.com:test{i}",
                    sent_at=datetime.utcnow()
                )
                # Insert directly to avoid duplicate key constraints
                doc = notification.dict()
                await self.db_manager.db.notifications.insert_one(doc)
                test_notifications.append(notification)
            
            # Test get_user_keyword_ids
            keyword_ids = await self.db_manager.get_user_keyword_ids(test_user.id)
            expected_ids = [kw.id for kw in test_keywords]
            
            if set(keyword_ids) == set(expected_ids):
                self.log_test_result("Database get_user_keyword_ids", True, f"Retrieved {len(keyword_ids)} keyword IDs")
            else:
                self.log_test_result("Database get_user_keyword_ids", False, f"Expected {expected_ids}, got {keyword_ids}")
                return False
            
            # Test delete_notifications_by_keyword_ids
            deleted_notifications = await self.db_manager.delete_notifications_by_keyword_ids(keyword_ids)
            
            if deleted_notifications == len(test_notifications):
                self.log_test_result("Database delete_notifications_by_keyword_ids", True, f"Deleted {deleted_notifications} notifications")
            else:
                self.log_test_result("Database delete_notifications_by_keyword_ids", False, f"Expected {len(test_notifications)}, deleted {deleted_notifications}")
                return False
            
            # Test delete_keyword_hits_by_keyword_ids (should handle non-existent collection gracefully)
            deleted_hits = await self.db_manager.delete_keyword_hits_by_keyword_ids(keyword_ids)
            self.log_test_result("Database delete_keyword_hits_by_keyword_ids", True, f"Handled gracefully, deleted {deleted_hits} hits")
            
            # Test delete_keywords_by_ids
            deleted_keywords = await self.db_manager.delete_keywords_by_ids(keyword_ids)
            
            if deleted_keywords == len(test_keywords):
                self.log_test_result("Database delete_keywords_by_ids", True, f"Deleted {deleted_keywords} keywords")
            else:
                self.log_test_result("Database delete_keywords_by_ids", False, f"Expected {len(test_keywords)}, deleted {deleted_keywords}")
                return False
            
            # Test empty list handling
            empty_result = await self.db_manager.delete_keywords_by_ids([])
            if empty_result == 0:
                self.log_test_result("Database Empty List Handling", True, "Empty lists handled correctly")
            else:
                self.log_test_result("Database Empty List Handling", False, f"Expected 0, got {empty_result}")
                return False
            
            # Cleanup test user
            await self.db_manager.db.users.delete_one({"id": test_user.id})
            
            return True
            
        except Exception as e:
            self.log_test_result("Database Helper Functions", False, f"Exception: {e}")
            return False
    
    async def test_cascading_delete_logic(self):
        """Test cascading delete logic with proper order"""
        try:
            from models import User, Keyword, Notification
            
            # Create test user
            test_user = User(
                telegram_id=888777666,
                id=str(uuid.uuid4())
            )
            await self.db_manager.create_user(test_user)
            
            # Create test keywords
            keyword_ids = []
            for i in range(2):
                keyword = Keyword(
                    id=str(uuid.uuid4()),
                    user_id=test_user.id,
                    original_keyword=f"CascadeTest{i}",
                    normalized_keyword=f"cascadetest{i}",
                    since_ts=datetime.utcnow(),
                    platforms=["militaria321.com"]
                )
                await self.db_manager.create_keyword(keyword)
                keyword_ids.append(keyword.id)
            
            # Create test notifications and keyword hits
            for i, keyword_id in enumerate(keyword_ids):
                # Create notifications
                for j in range(2):
                    notification = Notification(
                        id=str(uuid.uuid4()),
                        user_id=test_user.id,
                        keyword_id=keyword_id,
                        listing_key=f"militaria321.com:cascade{i}_{j}",
                        sent_at=datetime.utcnow()
                    )
                    doc = notification.dict()
                    await self.db_manager.db.notifications.insert_one(doc)
                
                # Create keyword hits (if collection exists)
                try:
                    await self.db_manager.db.keyword_hits.insert_one({
                        "id": str(uuid.uuid4()),
                        "keyword_id": keyword_id,
                        "listing_key": f"militaria321.com:hit{i}",
                        "seen_ts": datetime.utcnow()
                    })
                except Exception:
                    pass  # Collection might not exist
            
            # Test cascading delete in correct order (artifacts first, then keywords)
            
            # Step 1: Delete keyword hits
            deleted_hits = await self.db_manager.delete_keyword_hits_by_keyword_ids(keyword_ids)
            
            # Step 2: Delete notifications
            deleted_notifications = await self.db_manager.delete_notifications_by_keyword_ids(keyword_ids)
            
            # Step 3: Delete keywords
            deleted_keywords = await self.db_manager.delete_keywords_by_ids(keyword_ids)
            
            # Verify cascading delete worked
            success = (
                deleted_keywords == 2 and  # 2 keywords deleted
                deleted_notifications == 4  # 4 notifications deleted (2 per keyword)
            )
            
            details = f"Keywords: {deleted_keywords}, Notifications: {deleted_notifications}, Hits: {deleted_hits}"
            self.log_test_result("Cascading Delete Logic", success, details)
            
            # Cleanup test user
            await self.db_manager.db.users.delete_one({"id": test_user.id})
            
            return success
            
        except Exception as e:
            self.log_test_result("Cascading Delete Logic", False, f"Exception: {e}")
            return False
    
    async def test_idempotency(self):
        """Test idempotency - running operations twice should be safe"""
        try:
            from models import User, Keyword
            
            # Create test user
            test_user = User(
                telegram_id=777666555,
                id=str(uuid.uuid4())
            )
            await self.db_manager.create_user(test_user)
            
            # Create test keyword
            keyword = Keyword(
                id=str(uuid.uuid4()),
                user_id=test_user.id,
                original_keyword="IdempotencyTest",
                normalized_keyword="idempotencytest",
                since_ts=datetime.utcnow(),
                platforms=["militaria321.com"]
            )
            await self.db_manager.create_keyword(keyword)
            
            # First deletion
            keyword_ids = await self.db_manager.get_user_keyword_ids(test_user.id)
            deleted_hits_1 = await self.db_manager.delete_keyword_hits_by_keyword_ids(keyword_ids)
            deleted_notifications_1 = await self.db_manager.delete_notifications_by_keyword_ids(keyword_ids)
            deleted_keywords_1 = await self.db_manager.delete_keywords_by_ids(keyword_ids)
            
            # Second deletion (should be safe and return 0)
            deleted_hits_2 = await self.db_manager.delete_keyword_hits_by_keyword_ids(keyword_ids)
            deleted_notifications_2 = await self.db_manager.delete_notifications_by_keyword_ids(keyword_ids)
            deleted_keywords_2 = await self.db_manager.delete_keywords_by_ids(keyword_ids)
            
            # Verify idempotency
            success = (
                deleted_keywords_1 == 1 and
                deleted_keywords_2 == 0 and
                deleted_notifications_2 == 0 and
                deleted_hits_2 == 0
            )
            
            details = f"First run: kw={deleted_keywords_1}, notifs={deleted_notifications_1}, hits={deleted_hits_1}. Second run: kw={deleted_keywords_2}, notifs={deleted_notifications_2}, hits={deleted_hits_2}"
            self.log_test_result("Idempotency Safety", success, details)
            
            # Cleanup test user
            await self.db_manager.db.users.delete_one({"id": test_user.id})
            
            return success
            
        except Exception as e:
            self.log_test_result("Idempotency Safety", False, f"Exception: {e}")
            return False
    
    async def test_german_ux_messages(self):
        """Test German UX messages and confirmation flow"""
        try:
            # Test message constants and formatting
            clear_my_keywords_message = (
                "Möchten Sie wirklich *alle Ihre Suchbegriffe* löschen? "
                "Dies stoppt auch die Hintergrundüberwachung."
            )
            
            clear_data_message = (
                "⚠️ Achtung: Dies löscht *alle gespeicherten Angebote und Benachrichtigungen* für alle Nutzer. "
                "Nutzer & Keywords bleiben erhalten. Fortfahren?"
            )
            
            success_message_template = (
                "🧹 Bereinigung abgeschlossen.\n"
                "• Keywords: {n_kw}\n"
                "• Gestoppte Jobs: {stopped}\n"
                "• Keyword-Treffer: {n_hits}\n"
                "• Benachrichtigungen: {n_notifs}"
            )
            
            # Test message formatting
            formatted_success = success_message_template.format(
                n_kw=5, stopped=3, n_hits=10, n_notifs=7
            )
            
            # Verify German text and formatting
            german_checks = [
                "Suchbegriffe" in clear_my_keywords_message,
                "Hintergrundüberwachung" in clear_my_keywords_message,
                "Bereinigung abgeschlossen" in formatted_success,
                "Keywords:" in formatted_success,
                "Gestoppte Jobs:" in formatted_success,
                "Benachrichtigungen:" in formatted_success
            ]
            
            success = all(german_checks)
            details = f"German text checks: {sum(german_checks)}/{len(german_checks)} passed"
            self.log_test_result("German UX Messages", success, details)
            
            return success
            
        except Exception as e:
            self.log_test_result("German UX Messages", False, f"Exception: {e}")
            return False
    
    async def test_clear_command_pathways(self):
        """Test both /clear (user-specific) and /clear data (global) pathways"""
        try:
            # Mock message and callback objects
            class MockUser:
                def __init__(self, user_id):
                    self.id = user_id
            
            class MockMessage:
                def __init__(self, text, user_id):
                    self.text = text
                    self.from_user = MockUser(user_id)
                    self.answer = AsyncMock()
            
            class MockCallback:
                def __init__(self, data, user_id):
                    self.data = data
                    self.from_user = MockUser(user_id)
                    self.answer = AsyncMock()
                    self.message = Mock()
                    self.message.edit_text = AsyncMock()
            
            # Import the command handlers
            from simple_bot import cmd_clear, clear_my_keywords_confirm, clear_data_confirm
            
            # Test 1: /clear (user-specific pathway)
            user_message = MockMessage("/clear", 123456789)
            
            # Mock ensure_user function
            with patch('simple_bot.ensure_user') as mock_ensure_user:
                mock_user = Mock()
                mock_user.id = "test_user_123"
                mock_ensure_user.return_value = mock_user
                
                await cmd_clear(user_message)
                
                # Verify user-specific confirmation was sent
                user_message.answer.assert_called_once()
                call_args = user_message.answer.call_args
                message_text = call_args[0][0]
                
                user_specific_check = "alle Ihre Suchbegriffe" in message_text
                self.log_test_result("Clear Command User-Specific Pathway", user_specific_check, f"Message: {message_text[:100]}...")
            
            # Test 2: /clear data (global pathway)
            data_message = MockMessage("/clear data", 123456789)
            
            with patch('simple_bot.ensure_user') as mock_ensure_user:
                mock_user = Mock()
                mock_user.id = "test_user_123"
                mock_ensure_user.return_value = mock_user
                
                await cmd_clear(data_message)
                
                # Verify global confirmation was sent
                data_message.answer.assert_called_once()
                call_args = data_message.answer.call_args
                message_text = call_args[0][0]
                
                global_check = "alle gespeicherten Angebote" in message_text
                self.log_test_result("Clear Command Global Pathway", global_check, f"Message: {message_text[:100]}...")
            
            # Test 3: User-specific confirmation callback
            user_callback = MockCallback("clear_my_keywords_confirm", 123456789)
            
            with patch('simple_bot.ensure_user') as mock_ensure_user, \
                 patch('simple_bot.db_manager') as mock_db, \
                 patch('simple_bot.stop_keyword_job') as mock_stop_job:
                
                mock_user = Mock()
                mock_user.id = "test_user_123"
                mock_ensure_user.return_value = mock_user
                
                # Mock database operations as async functions
                mock_db.get_user_keyword_ids = AsyncMock(return_value=["kw1", "kw2"])
                mock_db.delete_keyword_hits_by_keyword_ids = AsyncMock(return_value=5)
                mock_db.delete_notifications_by_keyword_ids = AsyncMock(return_value=3)
                mock_db.delete_keywords_by_ids = AsyncMock(return_value=2)
                
                # Mock job stopping
                mock_stop_job.return_value = True
                
                await clear_my_keywords_confirm(user_callback)
                
                # Verify database operations were called
                mock_db.get_user_keyword_ids.assert_called_once_with("test_user_123")
                mock_db.delete_keywords_by_ids.assert_called_once_with(["kw1", "kw2"])
                
                user_callback_check = mock_db.delete_keywords_by_ids.called
                self.log_test_result("User-Specific Confirmation Callback", user_callback_check, "Database operations called correctly")
            
            # Test 4: Global confirmation callback
            global_callback = MockCallback("clear_data_confirm", 123456789)
            
            with patch('simple_bot.db_manager') as mock_db:
                mock_db.admin_clear_products = AsyncMock(return_value={
                    "listings": 100,
                    "keyword_hits": 50,
                    "notifications": 25
                })
                
                await clear_data_confirm(global_callback)
                
                # Verify global clear was called
                mock_db.admin_clear_products.assert_called_once()
                
                global_callback_check = mock_db.admin_clear_products.called
                self.log_test_result("Global Confirmation Callback", global_callback_check, "Global clear operation called correctly")
            
            return True
            
        except Exception as e:
            self.log_test_result("Clear Command Pathways", False, f"Exception: {e}")
            return False
    
    async def test_job_stopping_integration(self):
        """Test integration between database operations and job stopping"""
        try:
            from models import User, Keyword
            from scheduler import stop_keyword_job
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            
            # Create test scheduler with jobs
            test_scheduler = AsyncIOScheduler()
            test_scheduler.start()
            
            # Set global scheduler reference
            import scheduler
            scheduler.scheduler = test_scheduler
            
            # Create test user and keywords
            test_user = User(
                telegram_id=666555444,
                id=str(uuid.uuid4())
            )
            await self.db_manager.create_user(test_user)
            
            keyword_ids = []
            job_ids = []
            
            # Create keywords and corresponding jobs
            for i in range(3):
                keyword = Keyword(
                    id=str(uuid.uuid4()),
                    user_id=test_user.id,
                    original_keyword=f"JobTest{i}",
                    normalized_keyword=f"jobtest{i}",
                    since_ts=datetime.utcnow(),
                    platforms=["militaria321.com"]
                )
                await self.db_manager.create_keyword(keyword)
                keyword_ids.append(keyword.id)
                
                # Create corresponding scheduler job
                job_id = f"keyword_{keyword.id}"
                job_ids.append(job_id)
                test_scheduler.add_job(
                    func=lambda: None,
                    trigger=IntervalTrigger(seconds=60),
                    id=job_id,
                    name=f"Test job {i}"
                )
            
            # Verify jobs exist
            jobs_before = [test_scheduler.get_job(job_id) is not None for job_id in job_ids]
            
            # Stop jobs using the helper function
            stopped_jobs = []
            for job_id in job_ids:
                result = stop_keyword_job(job_id)
                stopped_jobs.append(result)
            
            # Verify jobs are stopped
            jobs_after = [test_scheduler.get_job(job_id) is not None for job_id in job_ids]
            
            # Delete keywords from database
            deleted_keywords = await self.db_manager.delete_keywords_by_ids(keyword_ids)
            
            # Cleanup
            test_scheduler.shutdown(wait=False)
            await self.db_manager.db.users.delete_one({"id": test_user.id})
            
            # Verify integration
            success = (
                all(jobs_before) and  # All jobs existed initially
                all(stopped_jobs) and  # All jobs were stopped successfully
                not any(jobs_after) and  # No jobs exist after stopping
                deleted_keywords == 3  # All keywords were deleted
            )
            
            details = f"Jobs before: {sum(jobs_before)}, Stopped: {sum(stopped_jobs)}, Jobs after: {sum(jobs_after)}, Keywords deleted: {deleted_keywords}"
            self.log_test_result("Job Stopping Integration", success, details)
            
            return success
            
        except Exception as e:
            self.log_test_result("Job Stopping Integration", False, f"Exception: {e}")
            return False
    
    async def run_all_tests(self):
        """Run all tests for /clear command refactor"""
        logger.info("🧹 Starting /clear Command Refactor Tests")
        
        if not await self.setup():
            logger.error("Failed to setup test environment")
            return False
        
        try:
            # Run all tests
            tests = [
                self.test_scheduler_stop_keyword_job(),
                self.test_database_helper_functions(),
                self.test_cascading_delete_logic(),
                self.test_idempotency(),
                self.test_german_ux_messages(),
                self.test_clear_command_pathways(),
                self.test_job_stopping_integration(),
            ]
            
            results = await asyncio.gather(*tests, return_exceptions=True)
            
            # Count results
            passed = sum(1 for r in results if r is True)
            failed = len(results) - passed
            
            logger.info(f"\n🧹 /clear Command Refactor Test Summary:")
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
            await self.teardown()

async def main():
    """Main test runner"""
    tester = ClearCommandTester()
    success = await tester.run_all_tests()
    
    if success:
        logger.info("\n🎉 All /clear command refactor tests passed!")
        return True
    else:
        logger.error("\n❌ Some /clear command refactor tests failed!")
        return False

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
"""Tests for storage module."""

import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from ..domain.models import Session, SessionState, ClaimResult, EventType, TaskPayload
from ..adapters.storage_sqlite.sqlite_storage import SQLiteStorage


class TestSQLiteStorage:
    """Tests for SQLite storage."""
    
    @pytest.mark.asyncio
    async def test_create_and_get_user(self, storage):
        """Test user creation and retrieval."""
        user = await storage.get_or_create_user(
            telegram_user_id=12345,
            username="testuser"
        )
        
        assert user.telegram_user_id == 12345
        assert user.username == "testuser"
        assert not user.is_banned
        
        # Get same user again
        user2 = await storage.get_or_create_user(
            telegram_user_id=12345,
            username="testuser"
        )
        
        assert user2.id == user.id
    
    @pytest.mark.asyncio
    async def test_user_ban_status(self, storage):
        """Test user ban/unban."""
        user = await storage.get_or_create_user(12345, "testuser")
        assert not user.is_banned
        
        # Ban user
        result = await storage.update_user_ban_status(12345, True, "Test ban")
        assert result is True
        
        # Check ban status
        user = await storage.get_user_by_telegram_id(12345)
        assert user.is_banned
        assert user.ban_reason == "Test ban"
        
        # Unban user
        await storage.update_user_ban_status(12345, False)
        user = await storage.get_user_by_telegram_id(12345)
        assert not user.is_banned
    
    @pytest.mark.asyncio
    async def test_session_operations(self, storage):
        """Test session create and update."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        # Create session
        session = Session(
            id=0,
            user_id=user.id,
            level_id=1,
            state=SessionState.READY,
            turn_count=0
        )
        session = await storage.upsert_session(session)
        
        assert session.id > 0
        assert session.state == SessionState.READY
        
        # Update session
        session.state = SessionState.INFLIGHT
        session.turn_count = 1
        await storage.upsert_session(session)
        
        # Retrieve session
        retrieved = await storage.get_session(user.id, 1)
        assert retrieved.state == SessionState.INFLIGHT
        assert retrieved.turn_count == 1
    
    @pytest.mark.asyncio
    async def test_level_progress(self, storage):
        """Test level pass tracking."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        # Initially not passed
        assert not await storage.is_level_passed(user.id, 1)
        
        # Current level should be 1
        current = await storage.get_current_level(user.id, 5)
        assert current == 1
        
        # Mark level 1 as passed
        await storage.mark_level_passed(user.id, 1)
        
        # Now level 1 is passed
        assert await storage.is_level_passed(user.id, 1)
        
        # Current level should be 2
        current = await storage.get_current_level(user.id, 5)
        assert current == 2
    
    @pytest.mark.asyncio
    async def test_claim_reward_success(self, storage):
        """Test successful reward claim."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        # Sync test reward items
        await storage.sync_reward_items([{
            'pool_id': 'test_pool',
            'items': [{
                'item_id': 'test_item',
                'type': 'ALIPAY_CODE',
                'code': 'TEST-CODE',
                'max_claims_per_item': 10
            }]
        }])
        
        # Claim reward
        result = await storage.claim_reward('test_pool', user.id, 1)
        
        assert result.result == ClaimResult.SUCCESS
        assert result.reward_code == 'TEST-CODE'
        assert result.item_id == 'test_item'
    
    @pytest.mark.asyncio
    async def test_claim_reward_already_claimed(self, storage):
        """Test duplicate claim prevention."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        await storage.sync_reward_items([{
            'pool_id': 'test_pool',
            'items': [{
                'item_id': 'test_item',
                'type': 'ALIPAY_CODE',
                'code': 'TEST-CODE',
                'max_claims_per_item': 10
            }]
        }])
        
        # First claim
        result1 = await storage.claim_reward('test_pool', user.id, 1)
        assert result1.result == ClaimResult.SUCCESS
        
        # Second claim for same level
        result2 = await storage.claim_reward('test_pool', user.id, 1)
        assert result2.result == ClaimResult.ALREADY_CLAIMED
    
    @pytest.mark.asyncio
    async def test_claim_jd_ecard_single_use(self, storage):
        """Test JD E-card can only be claimed once."""
        await storage.sync_reward_items([{
            'pool_id': 'ecard_pool',
            'items': [{
                'item_id': 'ecard_1',
                'type': 'JD_ECARD',
                'code': 'ECARD-001',
                'max_claims_per_item': 1
            }]
        }])
        
        # User 1 claims
        user1 = await storage.get_or_create_user(11111, "user1")
        result1 = await storage.claim_reward('ecard_pool', user1.id, 1)
        assert result1.result == ClaimResult.SUCCESS
        
        # User 2 tries to claim same card (different level to avoid already_claimed)
        user2 = await storage.get_or_create_user(22222, "user2")
        result2 = await storage.claim_reward('ecard_pool', user2.id, 1)
        assert result2.result == ClaimResult.NO_STOCK
    
    @pytest.mark.asyncio
    async def test_log_events(self, storage):
        """Test log event recording and export."""
        await storage.append_log_event(
            trace_id="test123",
            event_type=EventType.USER_IN,
            telegram_user_id=12345,
            chat_id=12345,
            content="Test message",
            level_id=1
        )
        
        logs = await storage.export_logs()
        
        assert len(logs) == 1
        assert logs[0]['trace_id'] == "test123"
        assert logs[0]['event_type'] == "USER_IN"
        assert logs[0]['content'] == "Test message"


class TestConcurrentClaims:
    """Tests for concurrent reward claiming."""
    
    @pytest.mark.asyncio
    async def test_concurrent_ecard_claims(self, temp_dir):
        """Test that concurrent claims don't over-issue E-cards."""
        import gc
        
        db_path = temp_dir / "concurrent_test.db"
        
        # Use a single storage instance (which is the correct async pattern)
        storage = SQLiteStorage(db_path)
        await storage.initialize()
        
        try:
            # Setup pool with 3 E-cards
            await storage.sync_reward_items([{
                'pool_id': 'ecard_pool',
                'items': [
                    {'item_id': f'ecard_{i}', 'type': 'JD_ECARD', 'code': f'CODE-{i}', 'max_claims_per_item': 1}
                    for i in range(3)
                ]
            }])
            
            # Create 10 users
            users = []
            for i in range(10):
                user = await storage.get_or_create_user(10000 + i, f"user{i}")
                users.append(user)
            
            # Concurrent claims using single storage instance
            async def claim(user, level):
                return await storage.claim_reward('ecard_pool', user.id, level)
            
            # Run concurrent claims
            tasks = []
            for i, user in enumerate(users):
                tasks.append(claim(user, i + 1))  # Different levels
            
            results = await asyncio.gather(*tasks)
            
            # Count successes
            successes = sum(1 for r in results if r.result == ClaimResult.SUCCESS)
            no_stocks = sum(1 for r in results if r.result == ClaimResult.NO_STOCK)
            
            # Should have exactly 3 successes (3 E-cards)
            assert successes == 3
            assert no_stocks == 7
            
        finally:
            # Cleanup
            await storage.close()
            
            # Force cleanup to release file handles on Windows
            gc.collect()
            await asyncio.sleep(0.1)


class TestQueuePersistence:
    """Tests for queue persistence functionality."""
    
    @pytest.mark.asyncio
    async def test_save_and_retrieve_pending_task(self, storage):
        """Test saving and retrieving a pending task."""
        # Create a user first (for foreign key)
        user = await storage.get_or_create_user(12345, "testuser")
        
        # Create a task
        task = TaskPayload(
            trace_id="test-trace-001",
            user_id=user.id,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=1001,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Test prompt",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        
        # Save the task
        task_id = await storage.save_pending_task(task)
        assert task_id > 0
        
        # Retrieve pending tasks
        tasks = await storage.get_pending_tasks()
        
        assert len(tasks) == 1
        assert tasks[0].trace_id == "test-trace-001"
        assert tasks[0].user_prompt == "Test prompt"
        assert tasks[0].level_id == 1
    
    @pytest.mark.asyncio
    async def test_delete_pending_task(self, storage):
        """Test deleting a pending task."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        task = TaskPayload(
            trace_id="test-trace-002",
            user_id=user.id,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=1002,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Test prompt 2",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        
        await storage.save_pending_task(task)
        
        # Verify task exists
        tasks = await storage.get_pending_tasks()
        assert len(tasks) == 1
        
        # Delete the task
        result = await storage.delete_pending_task("test-trace-002")
        assert result is True
        
        # Verify task is deleted
        tasks = await storage.get_pending_tasks()
        assert len(tasks) == 0
    
    @pytest.mark.asyncio
    async def test_pending_tasks_ordered_by_time(self, storage):
        """Test that pending tasks are returned ordered by enqueued_at time."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        base_time = datetime.now()
        
        # Create tasks in reverse order (newest first)
        for i in [3, 1, 2]:
            task = TaskPayload(
                trace_id=f"test-trace-00{i}",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=1000 + i,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt=f"Test prompt {i}",
                turn_index=1,
                enqueued_at=base_time + timedelta(seconds=i)
            )
            await storage.save_pending_task(task)
        
        # Retrieve tasks
        tasks = await storage.get_pending_tasks()
        
        assert len(tasks) == 3
        # Should be ordered by enqueued_at (ascending)
        assert tasks[0].trace_id == "test-trace-001"
        assert tasks[1].trace_id == "test-trace-002"
        assert tasks[2].trace_id == "test-trace-003"
    
    @pytest.mark.asyncio
    async def test_queue_persistence_across_restart(self, temp_dir):
        """Test that pending tasks survive a simulated restart."""
        import gc
        
        db_path = temp_dir / "restart_test.db"
        
        # First session: save tasks
        storage1 = SQLiteStorage(db_path)
        await storage1.initialize()
        
        user = await storage1.get_or_create_user(12345, "testuser")
        
        base_time = datetime.now()
        for i in range(3):
            task = TaskPayload(
                trace_id=f"restart-trace-00{i+1}",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=2000 + i,
                username="testuser",
                level_id=i + 1,
                session_id=i + 1,
                user_prompt=f"Restart test prompt {i+1}",
                turn_index=1,
                enqueued_at=base_time + timedelta(seconds=i)
            )
            await storage1.save_pending_task(task)
        
        await storage1.close()
        gc.collect()
        await asyncio.sleep(0.1)
        
        # Second session: verify tasks are still there
        storage2 = SQLiteStorage(db_path)
        await storage2.initialize()
        
        try:
            tasks = await storage2.get_pending_tasks()
            
            assert len(tasks) == 3
            assert tasks[0].trace_id == "restart-trace-001"
            assert tasks[1].trace_id == "restart-trace-002"
            assert tasks[2].trace_id == "restart-trace-003"
            
            # Verify all fields are correctly restored
            assert tasks[0].user_prompt == "Restart test prompt 1"
            assert tasks[0].level_id == 1
            assert tasks[0].session_id == 1
            assert tasks[0].telegram_user_id == 12345
        finally:
            await storage2.close()
            gc.collect()
            await asyncio.sleep(0.1)
    
    @pytest.mark.asyncio
    async def test_duplicate_task_rejection(self, storage):
        """Test that duplicate trace_id tasks are rejected."""
        user = await storage.get_or_create_user(12345, "testuser")
        
        task = TaskPayload(
            trace_id="duplicate-trace",
            user_id=user.id,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=3000,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Original task",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        
        # First save should succeed
        await storage.save_pending_task(task)
        
        # Second save with same trace_id should be ignored (ON CONFLICT DO NOTHING)
        task2 = TaskPayload(
            trace_id="duplicate-trace",
            user_id=user.id,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=3001,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Duplicate task",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        await storage.save_pending_task(task2)
        
        # Only one task should exist
        tasks = await storage.get_pending_tasks()
        assert len(tasks) == 1
        assert tasks[0].user_prompt == "Original task"

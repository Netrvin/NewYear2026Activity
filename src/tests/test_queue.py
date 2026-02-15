"""Tests for queue persistence and recovery."""

import pytest
import asyncio
import gc
from datetime import datetime, timedelta

from ..domain.models import TaskPayload
from ..adapters.queue_memory.memory_queue import MemoryQueue
from ..adapters.queue_memory.persistent_queue import PersistentQueue
from ..adapters.storage_sqlite.sqlite_storage import SQLiteStorage


class TestMemoryQueue:
    """Tests for memory queue."""
    
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self, memory_queue):
        """Test basic enqueue and dequeue."""
        task = TaskPayload(
            trace_id="test-001",
            user_id=1,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=1001,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Test",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        
        result = await memory_queue.enqueue(task)
        assert result is True
        assert memory_queue.qsize() == 1
        
        dequeued = await memory_queue.dequeue(timeout=1.0)
        assert dequeued is not None
        assert dequeued.trace_id == "test-001"
        assert memory_queue.qsize() == 0
    
    @pytest.mark.asyncio
    async def test_queue_full(self):
        """Test queue full behavior."""
        small_queue = MemoryQueue(max_size=2)
        
        for i in range(2):
            task = TaskPayload(
                trace_id=f"test-{i}",
                user_id=1,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=1000 + i,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt="Test",
                turn_index=1,
                enqueued_at=datetime.now()
            )
            await small_queue.enqueue(task)
        
        assert small_queue.is_full()
        
        # Third task should fail
        task3 = TaskPayload(
            trace_id="test-3",
            user_id=1,
            telegram_user_id=12345,
            chat_id=12345,
            message_id=1003,
            username="testuser",
            level_id=1,
            session_id=1,
            user_prompt="Test",
            turn_index=1,
            enqueued_at=datetime.now()
        )
        result = await small_queue.enqueue(task3)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_restore_from_storage(self, memory_queue):
        """Test restoring tasks from storage."""
        tasks = [
            TaskPayload(
                trace_id=f"restore-{i}",
                user_id=1,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=1000 + i,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt=f"Task {i}",
                turn_index=1,
                enqueued_at=datetime.now() + timedelta(seconds=i)
            )
            for i in range(3)
        ]
        
        restored = await memory_queue.restore_from_storage(tasks)
        
        assert restored == 3
        assert memory_queue.qsize() == 3


class TestPersistentQueue:
    """Tests for persistent queue."""
    
    @pytest.mark.asyncio
    async def test_enqueue_persists_to_storage(self, temp_dir):
        """Test that enqueue saves task to storage."""
        db_path = temp_dir / "persist_test.db"
        storage = SQLiteStorage(db_path)
        await storage.initialize()
        
        try:
            # Create user for foreign key
            user = await storage.get_or_create_user(12345, "testuser")
            
            queue = PersistentQueue(storage=storage, max_size=100)
            
            task = TaskPayload(
                trace_id="persist-001",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=1001,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt="Persistent task",
                turn_index=1,
                enqueued_at=datetime.now()
            )
            
            result = await queue.enqueue(task)
            assert result is True
            
            # Verify task is in storage
            pending = await storage.get_pending_tasks()
            assert len(pending) == 1
            assert pending[0].trace_id == "persist-001"
        finally:
            await storage.close()
            gc.collect()
            await asyncio.sleep(0.1)
    
    @pytest.mark.asyncio
    async def test_mark_completed_removes_from_storage(self, temp_dir):
        """Test that mark_completed removes task from storage."""
        db_path = temp_dir / "complete_test.db"
        storage = SQLiteStorage(db_path)
        await storage.initialize()
        
        try:
            user = await storage.get_or_create_user(12345, "testuser")
            queue = PersistentQueue(storage=storage, max_size=100)
            
            task = TaskPayload(
                trace_id="complete-001",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=1001,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt="Task to complete",
                turn_index=1,
                enqueued_at=datetime.now()
            )
            
            await queue.enqueue(task)
            
            # Verify in storage
            pending = await storage.get_pending_tasks()
            assert len(pending) == 1
            
            # Mark as completed
            await queue.mark_completed("complete-001")
            
            # Verify removed from storage
            pending = await storage.get_pending_tasks()
            assert len(pending) == 0
        finally:
            await storage.close()
            gc.collect()
            await asyncio.sleep(0.1)
    
    @pytest.mark.asyncio
    async def test_queue_recovery_on_restart(self, temp_dir):
        """Test full queue recovery scenario on restart."""
        db_path = temp_dir / "recovery_test.db"
        
        # Session 1: Enqueue tasks and "crash"
        storage1 = SQLiteStorage(db_path)
        await storage1.initialize()
        
        user = await storage1.get_or_create_user(12345, "testuser")
        queue1 = PersistentQueue(storage=storage1, max_size=100)
        
        base_time = datetime.now()
        for i in range(3):
            task = TaskPayload(
                trace_id=f"recovery-{i+1}",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=2000 + i,
                username="testuser",
                level_id=i + 1,
                session_id=i + 1,
                user_prompt=f"Recovery task {i+1}",
                turn_index=1,
                enqueued_at=base_time + timedelta(seconds=i)
            )
            await queue1.enqueue(task)
        
        # Verify all tasks in memory queue
        assert queue1.qsize() == 3
        
        # Close storage (simulate crash)
        await storage1.close()
        gc.collect()
        await asyncio.sleep(0.1)
        
        # Session 2: Recover tasks
        storage2 = SQLiteStorage(db_path)
        await storage2.initialize()
        
        try:
            # New queue starts empty
            queue2 = PersistentQueue(storage=storage2, max_size=100)
            assert queue2.qsize() == 0
            
            # Restore from storage
            pending = await storage2.get_pending_tasks()
            assert len(pending) == 3
            
            restored = await queue2.restore_from_storage(pending)
            assert restored == 3
            assert queue2.qsize() == 3
            
            # Verify order (should be by enqueued_at)
            task1 = await queue2.dequeue(timeout=1.0)
            assert task1 is not None
            assert task1.trace_id == "recovery-1"
            assert task1.user_prompt == "Recovery task 1"
            
            task2 = await queue2.dequeue(timeout=1.0)
            assert task2 is not None
            assert task2.trace_id == "recovery-2"
            
            task3 = await queue2.dequeue(timeout=1.0)
            assert task3 is not None
            assert task3.trace_id == "recovery-3"
            
            # Mark all as completed
            await queue2.mark_completed("recovery-1")
            await queue2.mark_completed("recovery-2")
            await queue2.mark_completed("recovery-3")
            
            # Verify storage is now empty
            pending = await storage2.get_pending_tasks()
            assert len(pending) == 0
        finally:
            await storage2.close()
            gc.collect()
            await asyncio.sleep(0.1)
    
    @pytest.mark.asyncio
    async def test_partial_processing_recovery(self, temp_dir):
        """Test recovery when only some tasks were processed before crash."""
        db_path = temp_dir / "partial_test.db"
        
        # Session 1: Enqueue 5 tasks, process 2, then "crash"
        storage1 = SQLiteStorage(db_path)
        await storage1.initialize()
        
        user = await storage1.get_or_create_user(12345, "testuser")
        queue1 = PersistentQueue(storage=storage1, max_size=100)
        
        base_time = datetime.now()
        for i in range(5):
            task = TaskPayload(
                trace_id=f"partial-{i+1}",
                user_id=user.id,
                telegram_user_id=12345,
                chat_id=12345,
                message_id=3000 + i,
                username="testuser",
                level_id=1,
                session_id=1,
                user_prompt=f"Partial task {i+1}",
                turn_index=1,
                enqueued_at=base_time + timedelta(seconds=i)
            )
            await queue1.enqueue(task)
        
        # Process 2 tasks
        task1 = await queue1.dequeue(timeout=1.0)
        assert task1 is not None
        await queue1.mark_completed(task1.trace_id)
        
        task2 = await queue1.dequeue(timeout=1.0)
        assert task2 is not None
        await queue1.mark_completed(task2.trace_id)
        
        # Verify: 3 tasks in queue, 3 in storage
        assert queue1.qsize() == 3
        pending = await storage1.get_pending_tasks()
        assert len(pending) == 3
        
        # Close (crash)
        await storage1.close()
        gc.collect()
        await asyncio.sleep(0.1)
        
        # Session 2: Should recover only 3 tasks
        storage2 = SQLiteStorage(db_path)
        await storage2.initialize()
        
        try:
            pending = await storage2.get_pending_tasks()
            assert len(pending) == 3
            
            # Verify correct tasks remain (3, 4, 5)
            trace_ids = [t.trace_id for t in pending]
            assert "partial-3" in trace_ids
            assert "partial-4" in trace_ids
            assert "partial-5" in trace_ids
            assert "partial-1" not in trace_ids
            assert "partial-2" not in trace_ids
        finally:
            await storage2.close()
            gc.collect()
            await asyncio.sleep(0.1)

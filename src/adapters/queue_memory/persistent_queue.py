"""Persistent queue implementation with SQLite backing."""

import asyncio
import logging
from typing import Optional, List, TYPE_CHECKING

from ...ports.queue import IQueue
from ...domain.models import TaskPayload

if TYPE_CHECKING:
    from ...ports.storage import IStorage

logger = logging.getLogger(__name__)


class PersistentQueue(IQueue):
    """Memory queue with persistent backing for crash recovery.
    
    Tasks are saved to storage when enqueued and deleted when completed.
    On startup, pending tasks can be restored from storage.
    """
    
    def __init__(self, storage: 'IStorage', max_size: int = 20000):
        self._storage = storage
        self._queue: asyncio.Queue[TaskPayload] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
    
    async def enqueue(self, task: TaskPayload) -> bool:
        """Add a task to the queue and persist it."""
        try:
            # First persist to storage
            await self._storage.save_pending_task(task)
            
            # Then add to memory queue
            self._queue.put_nowait(task)
            logger.debug(f"Task {task.trace_id} enqueued and persisted")
            return True
        except asyncio.QueueFull:
            # Remove from storage if queue is full
            await self._storage.delete_pending_task(task.trace_id)
            logger.warning(f"Queue full, task {task.trace_id} rejected")
            return False
        except Exception as e:
            logger.error(f"Error enqueuing task {task.trace_id}: {e}")
            return False
    
    async def dequeue(self, timeout: Optional[float] = None) -> Optional[TaskPayload]:
        """Get next task from the queue."""
        try:
            if timeout:
                task = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                task = await self._queue.get()
            return task
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None
    
    def qsize(self) -> int:
        """Get approximate queue size."""
        return self._queue.qsize()
    
    def is_full(self) -> bool:
        """Check if queue is at capacity."""
        return self._queue.full()
    
    async def clear(self) -> None:
        """Clear all items from the queue."""
        while not self._queue.empty():
            try:
                task = self._queue.get_nowait()
                # Also delete from storage
                await self._storage.delete_pending_task(task.trace_id)
            except asyncio.QueueEmpty:
                break
    
    async def restore_from_storage(self, tasks: List[TaskPayload]) -> int:
        """Restore tasks from persistent storage.
        
        Args:
            tasks: List of tasks to restore, sorted by enqueued_at
            
        Returns:
            Number of tasks successfully restored
        """
        restored_count = 0
        for task in tasks:
            try:
                self._queue.put_nowait(task)
                restored_count += 1
                logger.info(f"Restored task {task.trace_id} from storage")
            except asyncio.QueueFull:
                logger.warning(f"Queue full during restore, skipping remaining {len(tasks) - restored_count} tasks")
                break
        
        if restored_count > 0:
            logger.info(f"Restored {restored_count} pending tasks from storage")
        
        return restored_count
    
    async def mark_completed(self, trace_id: str) -> None:
        """Mark a task as completed by removing from persistent storage."""
        await self._storage.delete_pending_task(trace_id)
        logger.debug(f"Task {trace_id} marked as completed and removed from storage")
    
    def task_done(self) -> None:
        """Mark a task as done (for proper queue management)."""
        self._queue.task_done()

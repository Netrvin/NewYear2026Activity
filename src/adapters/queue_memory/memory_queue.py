"""Memory-based queue implementation."""

import asyncio
from typing import Optional, List

from ...ports.queue import IQueue
from ...domain.models import TaskPayload


class MemoryQueue(IQueue):
    """In-memory queue implementation using asyncio.Queue."""
    
    def __init__(self, max_size: int = 20000):
        self._queue: asyncio.Queue[TaskPayload] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
    
    async def enqueue(self, task: TaskPayload) -> bool:
        """Add a task to the queue."""
        try:
            # Use put_nowait to avoid blocking, but handle full queue
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
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
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    async def restore_from_storage(self, tasks: List[TaskPayload]) -> int:
        """Restore tasks from persistent storage.
        
        For MemoryQueue, this simply adds the tasks to the queue.
        """
        restored_count = 0
        for task in tasks:
            try:
                self._queue.put_nowait(task)
                restored_count += 1
            except asyncio.QueueFull:
                break
        return restored_count
    
    async def mark_completed(self, trace_id: str) -> None:
        """Mark a task as completed. No-op for memory queue."""
        pass
    
    def task_done(self) -> None:
        """Mark a task as done (for proper queue management)."""
        self._queue.task_done()

"""Queue interface for task management."""

from abc import ABC, abstractmethod
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ports.storage import IStorage
from ..domain.models import TaskPayload


class IQueue(ABC):
    """Interface for task queue."""
    
    @abstractmethod
    async def enqueue(self, task: TaskPayload) -> bool:
        """Add a task to the queue.
        
        Args:
            task: Task payload to enqueue
            
        Returns:
            True if successfully enqueued
        """
        pass
    
    @abstractmethod
    async def dequeue(self, timeout: Optional[float] = None) -> Optional[TaskPayload]:
        """Get next task from the queue.
        
        Args:
            timeout: Optional timeout in seconds
            
        Returns:
            TaskPayload or None if queue is empty/timeout
        """
        pass
    
    @abstractmethod
    def qsize(self) -> int:
        """Get approximate queue size.
        
        Returns:
            Number of items in queue
        """
        pass
    
    @abstractmethod
    def is_full(self) -> bool:
        """Check if queue is at capacity.
        
        Returns:
            True if queue is full
        """
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """Clear all items from the queue."""
        pass
    
    @abstractmethod
    async def restore_from_storage(self, tasks: List[TaskPayload]) -> int:
        """Restore tasks from persistent storage.
        
        Args:
            tasks: List of tasks to restore, sorted by enqueued_at
            
        Returns:
            Number of tasks successfully restored
        """
        pass
    
    @abstractmethod
    async def mark_completed(self, trace_id: str) -> None:
        """Mark a task as completed (for persistence tracking).
        
        Args:
            trace_id: Trace ID of the completed task
        """
        pass

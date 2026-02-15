"""Worker pool for processing queued tasks."""

import asyncio
import logging
from typing import Optional, Callable, Awaitable

from ..ports.queue import IQueue
from ..domain.models import TaskPayload

logger = logging.getLogger(__name__)


class WorkerPool:
    """Pool of workers consuming tasks from queue."""
    
    def __init__(
        self,
        queue: IQueue,
        processor: Callable[[TaskPayload], Awaitable[None]],
        concurrency: int = 8
    ):
        self.queue = queue
        self.processor = processor
        self.concurrency = concurrency
        
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._active_count = 0
    
    async def start(self) -> None:
        """Start the worker pool."""
        self._running = True
        
        for i in range(self.concurrency):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        
        logger.info(f"Worker pool started with {self.concurrency} workers")
    
    async def stop(self) -> None:
        """Stop the worker pool gracefully."""
        self._running = False
        
        # Wait for workers to finish current tasks
        for worker in self._workers:
            worker.cancel()
        
        # Wait for all workers to complete
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        
        self._workers.clear()
        logger.info("Worker pool stopped")
    
    async def _worker_loop(self, worker_id: int) -> None:
        """Main loop for a single worker."""
        logger.debug(f"Worker {worker_id} started")
        
        while self._running:
            try:
                # Try to get a task with timeout
                task = await self.queue.dequeue(timeout=1.0)
                
                if task is None:
                    continue
                
                self._active_count += 1
                logger.debug(f"Worker {worker_id} processing task {task.trace_id}")
                
                try:
                    await self.processor(task)
                    # Mark task as completed in persistent storage
                    await self.queue.mark_completed(task.trace_id)
                except Exception as e:
                    logger.exception(f"Worker {worker_id} error processing task {task.trace_id}: {e}")
                    # Also mark as completed on error to prevent infinite retries
                    # The session state will indicate the failure
                    await self.queue.mark_completed(task.trace_id)
                finally:
                    self._active_count -= 1
                    # Mark task as done if queue supports it
                    if hasattr(self.queue, 'task_done'):
                        self.queue.task_done()
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Worker {worker_id} unexpected error: {e}")
                await asyncio.sleep(1)  # Prevent tight loop on errors
        
        logger.debug(f"Worker {worker_id} stopped")
    
    @property
    def active_workers(self) -> int:
        """Get number of actively processing workers."""
        return self._active_count
    
    @property
    def queue_size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()

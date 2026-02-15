"""Storage interface for data persistence."""

from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime
from ..domain.models import (
    User, Session, Attempt, LevelProgress, RewardClaim, 
    LogEvent, RewardClaimResponse, SessionState, EventType,
    TaskPayload
)


class IStorage(ABC):
    """Interface for data storage (SQLite, PostgreSQL, etc.)."""
    
    # ==================== User Operations ====================
    
    @abstractmethod
    async def get_or_create_user(
        self, 
        telegram_user_id: int, 
        username: Optional[str]
    ) -> User:
        """Get existing user or create a new one.
        
        Args:
            telegram_user_id: Telegram user ID
            username: Telegram username
            
        Returns:
            User object
        """
        pass
    
    @abstractmethod
    async def get_user_by_telegram_id(self, telegram_user_id: int) -> Optional[User]:
        """Get user by Telegram ID.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            User object or None
        """
        pass
    
    @abstractmethod
    async def update_user_ban_status(
        self, 
        telegram_user_id: int, 
        is_banned: bool, 
        reason: Optional[str] = None
    ) -> bool:
        """Update user's ban status.
        
        Args:
            telegram_user_id: Telegram user ID
            is_banned: Whether user should be banned
            reason: Ban reason
            
        Returns:
            True if user was found and updated
        """
        pass
    
    # ==================== Session Operations ====================
    
    @abstractmethod
    async def get_session(self, user_id: int, level_id: int) -> Optional[Session]:
        """Get user's session for a specific level.
        
        Args:
            user_id: Internal user ID
            level_id: Level ID
            
        Returns:
            Session object or None
        """
        pass
    
    @abstractmethod
    async def upsert_session(self, session: Session) -> Session:
        """Create or update a session.
        
        Args:
            session: Session object to save
            
        Returns:
            Saved session with ID
        """
        pass
    
    @abstractmethod
    async def set_session_inflight(self, session_id: int) -> bool:
        """Mark session as inflight (processing).
        
        Args:
            session_id: Session ID
            
        Returns:
            True if successfully set to inflight
        """
        pass
    
    @abstractmethod
    async def reset_session(self, user_id: int, level_id: int) -> bool:
        """Reset a user's session for a specific level.
        
        Args:
            user_id: Internal user ID
            level_id: Level ID
            
        Returns:
            True if session was found and reset
        """
        pass
    
    @abstractmethod
    async def clear_session_inflight(
        self, 
        session_id: int, 
        new_state: SessionState,
        cooldown_until: Optional[datetime] = None
    ) -> bool:
        """Clear inflight status and set new state.
        
        Args:
            session_id: Session ID
            new_state: New session state
            cooldown_until: Optional cooldown end time
            
        Returns:
            True if successfully updated
        """
        pass
    
    # ==================== Level Progress ====================
    
    @abstractmethod
    async def is_level_passed(self, user_id: int, level_id: int) -> bool:
        """Check if user has passed a level.
        
        Args:
            user_id: Internal user ID
            level_id: Level ID
            
        Returns:
            True if level is passed
        """
        pass
    
    @abstractmethod
    async def mark_level_passed(self, user_id: int, level_id: int) -> None:
        """Mark a level as passed for a user.
        
        Args:
            user_id: Internal user ID
            level_id: Level ID
        """
        pass
    
    @abstractmethod
    async def get_current_level(self, user_id: int, total_levels: int) -> int:
        """Get user's current level (first unpassed level).
        
        Args:
            user_id: Internal user ID
            total_levels: Total number of levels
            
        Returns:
            Current level ID (1-indexed)
        """
        pass
    
    @abstractmethod
    async def get_user_progress(self, user_id: int) -> List[LevelProgress]:
        """Get all level progress for a user.
        
        Args:
            user_id: Internal user ID
            
        Returns:
            List of LevelProgress objects
        """
        pass
    
    # ==================== Attempts ====================
    
    @abstractmethod
    async def record_attempt(self, attempt: Attempt) -> Attempt:
        """Record an attempt at a level.
        
        Args:
            attempt: Attempt object to save
            
        Returns:
            Saved attempt with ID
        """
        pass
    
    # ==================== Rewards ====================
    
    @abstractmethod
    async def claim_reward(
        self, 
        pool_id: str, 
        user_id: int, 
        level_id: int
    ) -> RewardClaimResponse:
        """Atomically claim a reward from a pool.
        
        This must be atomic and prevent:
        - Duplicate claims by same user for same level
        - Over-claiming items beyond max_claims_per_item
        - Race conditions in concurrent scenarios
        
        Args:
            pool_id: Reward pool ID
            user_id: Internal user ID
            level_id: Level ID
            
        Returns:
            RewardClaimResponse with result and reward code if successful
        """
        pass
    
    @abstractmethod
    async def get_user_claims(self, user_id: int) -> List[RewardClaim]:
        """Get all reward claims for a user.
        
        Args:
            user_id: Internal user ID
            
        Returns:
            List of RewardClaim objects
        """
        pass
    
    # ==================== Logging ====================
    
    @abstractmethod
    async def append_log_event(
        self,
        trace_id: str,
        event_type: EventType,
        telegram_user_id: int,
        chat_id: int,
        content: str,
        level_id: Optional[int] = None,
        session_id: Optional[int] = None,
        turn_index: Optional[int] = None,
        metadata: Optional[dict] = None
    ) -> None:
        """Append a log event.
        
        Args:
            trace_id: Trace ID for request correlation
            event_type: Type of event
            telegram_user_id: Telegram user ID
            chat_id: Chat ID
            content: Event content (truncated if needed)
            level_id: Optional level ID
            session_id: Optional session ID
            turn_index: Optional turn index
            metadata: Optional additional metadata
        """
        pass
    
    @abstractmethod
    async def export_logs(
        self, 
        date: Optional[datetime] = None,
        mask_codes: bool = True
    ) -> List[dict]:
        """Export logs for review.
        
        Args:
            date: Optional date to filter logs
            mask_codes: Whether to mask reward codes
            
        Returns:
            List of log entries as dictionaries
        """
        pass
    
    # ==================== Stats ====================
    
    @abstractmethod
    async def get_stats(self) -> dict:
        """Get activity statistics.
        
        Returns:
            Dictionary with stats (today's requests, claims, etc.)
        """
        pass
    
    # ==================== Reward Items Management ====================
    
    @abstractmethod
    async def sync_reward_items(self, pools: List[dict]) -> None:
        """Sync reward items from config to database.
        
        Args:
            pools: List of reward pool configurations
        """
        pass
    
    # ==================== Queue Persistence ====================
    
    @abstractmethod
    async def save_pending_task(self, task: TaskPayload) -> int:
        """Save a pending task to persistent storage.
        
        Args:
            task: Task payload to save
            
        Returns:
            Database ID of the saved task
        """
        pass
    
    @abstractmethod
    async def delete_pending_task(self, trace_id: str) -> bool:
        """Delete a pending task by trace_id.
        
        Args:
            trace_id: Trace ID of the task to delete
            
        Returns:
            True if task was found and deleted
        """
        pass
    
    @abstractmethod
    async def get_pending_tasks(self) -> List[TaskPayload]:
        """Get all pending tasks ordered by enqueued_at time.
        
        Returns:
            List of TaskPayload objects sorted by submission time
        """
        pass
    
    # ==================== Lifecycle ====================
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize storage (create tables, etc.)."""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close storage connections."""
        pass

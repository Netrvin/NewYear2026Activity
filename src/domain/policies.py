"""Policies for unlock, cooldown, and other game rules."""

from datetime import datetime, timedelta
from typing import Optional

from .models import Session, SessionState, User
from ..ports.content import LevelConfig, ActivityConfig


class UnlockPolicy:
    """Policy for level unlock rules."""
    
    @staticmethod
    def can_access_level(
        user_id: int,
        level_id: int,
        passed_levels: set[int],
        total_levels: int
    ) -> tuple[bool, str]:
        """Check if user can access a level.
        
        Sequential unlock: Must pass level N-1 to access level N.
        
        Returns:
            Tuple of (can_access, reason)
        """
        if level_id < 1 or level_id > total_levels:
            return False, f"Invalid level: {level_id}"
        
        if level_id == 1:
            return True, "Level 1 is always accessible"
        
        # Check if previous level is passed
        if level_id - 1 not in passed_levels:
            return False, f"请先通关第 {level_id - 1} 关"
        
        return True, "Level unlocked"


class CooldownPolicy:
    """Policy for cooldown after failures."""
    
    @staticmethod
    def is_in_cooldown(session: Session) -> tuple[bool, Optional[int]]:
        """Check if session is in cooldown.
        
        Returns:
            Tuple of (is_in_cooldown, remaining_seconds)
        """
        if session.state != SessionState.COOLDOWN:
            return False, None
        
        if not session.cooldown_until:
            return False, None
        
        now = datetime.now()
        if now >= session.cooldown_until:
            return False, None
        
        remaining = int((session.cooldown_until - now).total_seconds())
        return True, remaining
    
    @staticmethod
    def calculate_cooldown_until(
        cooldown_seconds: int,
        from_time: Optional[datetime] = None
    ) -> datetime:
        """Calculate when cooldown ends."""
        start = from_time or datetime.now()
        return start + timedelta(seconds=cooldown_seconds)


class TurnPolicy:
    """Policy for turn/attempt limits."""
    
    @staticmethod
    def can_attempt(session: Session, max_turns: int) -> tuple[bool, str]:
        """Check if user can make another attempt.
        
        Returns:
            Tuple of (can_attempt, reason)
        """
        if session.turn_count >= max_turns:
            return False, f"已达到最大尝试次数 ({max_turns} 次)"
        
        remaining = max_turns - session.turn_count
        return True, f"剩余 {remaining} 次机会"
    
    @staticmethod
    def get_remaining_turns(session: Session, max_turns: int) -> int:
        """Get remaining turns."""
        return max(0, max_turns - session.turn_count)


class InputValidationPolicy:
    """Policy for input validation."""
    
    @staticmethod
    def validate_input(
        text: str,
        max_chars: int,
        min_chars: int = 1
    ) -> tuple[bool, str]:
        """Validate user input.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not text or not text.strip():
            return False, "输入不能为空"
        
        text = text.strip()
        
        if len(text) < min_chars:
            return False, f"输入太短，至少需要 {min_chars} 个字符"
        
        if len(text) > max_chars:
            return False, f"输入超出限制，最多 {max_chars} 个字符，当前 {len(text)} 个字符"
        
        # Check for excessive newlines (optional rule)
        max_newlines = 100
        if text.count('\n') > max_newlines:
            return False, f"输入换行符过多，最多 {max_newlines} 个"
        
        return True, "Valid"


class ActivityTimePolicy:
    """Policy for activity time window."""
    
    @staticmethod
    def is_activity_active(config: ActivityConfig) -> tuple[bool, str]:
        """Check if activity is currently active.
        
        Returns:
            Tuple of (is_active, reason)
        """
        if not config.enabled:
            return False, "活动已关闭"
        
        now = datetime.now(config.start_at.tzinfo)
        
        if now < config.start_at:
            return False, f"活动尚未开始，将于 {config.start_at.strftime('%Y-%m-%d %H:%M')} 开始"
        
        if now > config.end_at:
            return False, f"活动已结束"
        
        return True, "Activity is active"


class RewardTimePolicy:
    """Policy for reward time window."""

    @staticmethod
    def is_reward_active(config: ActivityConfig) -> tuple[bool, str]:
        """Check if reward distribution is currently active.

        If reward_start_at / reward_end_at are not configured, the reward
        window is considered the same as the activity window.

        Returns:
            Tuple of (is_active, reason)
        """
        reward_start = config.reward_start_at or config.start_at
        reward_end = config.reward_end_at or config.end_at

        now = datetime.now(reward_start.tzinfo)

        if now < reward_start:
            return False, f"发奖尚未开始，将于 {reward_start.strftime('%Y-%m-%d %H:%M')} 开始"

        if now > reward_end:
            return False, "发奖已结束"

        return True, "Reward is active"


class BanPolicy:
    """Policy for user bans."""
    
    @staticmethod
    def is_banned(user: User) -> tuple[bool, str]:
        """Check if user is banned.
        
        Returns:
            Tuple of (is_banned, reason)
        """
        if user.is_banned:
            reason = user.ban_reason or "违规操作"
            return True, f"您已被封禁：{reason}"
        
        return False, "User is not banned"

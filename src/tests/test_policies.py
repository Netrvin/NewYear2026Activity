"""Tests for policies."""

import pytest
from datetime import datetime, timedelta

from ..domain.policies import (
    UnlockPolicy, CooldownPolicy, TurnPolicy,
    InputValidationPolicy, BanPolicy, RewardTimePolicy
)
from ..domain.models import Session, SessionState, User
from ..ports.content import ActivityConfig, ChannelConfig, GlobalLimits, LLMConfig


class TestUnlockPolicy:
    """Tests for UnlockPolicy."""
    
    def test_level_1_always_accessible(self):
        """Level 1 should always be accessible."""
        can_access, reason = UnlockPolicy.can_access_level(
            user_id=1,
            level_id=1,
            passed_levels=set(),
            total_levels=5
        )
        assert can_access is True
    
    def test_level_2_requires_level_1(self):
        """Level 2 requires level 1 to be passed."""
        # Without level 1 passed
        can_access, reason = UnlockPolicy.can_access_level(
            user_id=1,
            level_id=2,
            passed_levels=set(),
            total_levels=5
        )
        assert can_access is False
        assert "第 1 关" in reason
        
        # With level 1 passed
        can_access, reason = UnlockPolicy.can_access_level(
            user_id=1,
            level_id=2,
            passed_levels={1},
            total_levels=5
        )
        assert can_access is True
    
    def test_invalid_level(self):
        """Invalid level should be rejected."""
        can_access, reason = UnlockPolicy.can_access_level(
            user_id=1,
            level_id=99,
            passed_levels={1, 2, 3, 4, 5},
            total_levels=5
        )
        assert can_access is False


class TestCooldownPolicy:
    """Tests for CooldownPolicy."""
    
    def test_not_in_cooldown_when_ready(self):
        """READY state should not be in cooldown."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.READY, turn_count=0
        )
        
        is_cooling, remaining = CooldownPolicy.is_in_cooldown(session)
        assert is_cooling is False
    
    def test_in_cooldown_with_future_time(self):
        """Session with future cooldown_until should be in cooldown."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.COOLDOWN, turn_count=1,
            cooldown_until=datetime.now() + timedelta(seconds=10)
        )
        
        is_cooling, remaining = CooldownPolicy.is_in_cooldown(session)
        assert is_cooling is True
        assert remaining > 0
    
    def test_not_in_cooldown_after_expiry(self):
        """Session with past cooldown_until should not be in cooldown."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.COOLDOWN, turn_count=1,
            cooldown_until=datetime.now() - timedelta(seconds=10)
        )
        
        is_cooling, remaining = CooldownPolicy.is_in_cooldown(session)
        assert is_cooling is False


class TestTurnPolicy:
    """Tests for TurnPolicy."""
    
    def test_can_attempt_with_turns_remaining(self):
        """Should be able to attempt with turns remaining."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.READY, turn_count=2
        )
        
        can_attempt, reason = TurnPolicy.can_attempt(session, max_turns=5)
        assert can_attempt is True
        assert "3" in reason  # 3 remaining
    
    def test_cannot_attempt_at_max_turns(self):
        """Should not be able to attempt at max turns."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.READY, turn_count=5
        )
        
        can_attempt, reason = TurnPolicy.can_attempt(session, max_turns=5)
        assert can_attempt is False
    
    def test_remaining_turns(self):
        """Test remaining turns calculation."""
        session = Session(
            id=1, user_id=1, level_id=1,
            state=SessionState.READY, turn_count=3
        )
        
        remaining = TurnPolicy.get_remaining_turns(session, max_turns=5)
        assert remaining == 2


class TestInputValidationPolicy:
    """Tests for InputValidationPolicy."""
    
    def test_valid_input(self):
        """Valid input should pass."""
        is_valid, msg = InputValidationPolicy.validate_input(
            text="Hello world",
            max_chars=100
        )
        assert is_valid is True
    
    def test_empty_input(self):
        """Empty input should fail."""
        is_valid, msg = InputValidationPolicy.validate_input(
            text="",
            max_chars=100
        )
        assert is_valid is False
        assert "空" in msg
    
    def test_too_long_input(self):
        """Too long input should fail."""
        is_valid, msg = InputValidationPolicy.validate_input(
            text="x" * 200,
            max_chars=100
        )
        assert is_valid is False
        assert "超出" in msg
    
    def test_whitespace_only_input(self):
        """Whitespace-only input should fail."""
        is_valid, msg = InputValidationPolicy.validate_input(
            text="   \n\t  ",
            max_chars=100
        )
        assert is_valid is False


class TestBanPolicy:
    """Tests for BanPolicy."""
    
    def test_not_banned_user(self):
        """Non-banned user should pass."""
        user = User(
            id=1, telegram_user_id=12345,
            username="test", is_banned=False
        )
        
        is_banned, reason = BanPolicy.is_banned(user)
        assert is_banned is False
    
    def test_banned_user(self):
        """Banned user should fail."""
        user = User(
            id=1, telegram_user_id=12345,
            username="test", is_banned=True,
            ban_reason="Cheating"
        )
        
        is_banned, reason = BanPolicy.is_banned(user)
        assert is_banned is True
        assert "Cheating" in reason


def _make_activity_config(
    start_at: datetime,
    end_at: datetime,
    reward_start_at=None,
    reward_end_at=None,
):
    """Helper to create an ActivityConfig for testing."""
    return ActivityConfig(
        activity_id="test",
        title="Test",
        enabled=True,
        start_at=start_at,
        end_at=end_at,
        channel=ChannelConfig(name="test", bot_display_name="Bot"),
        global_limits=GlobalLimits(),
        llm=LLMConfig(provider="mock", model="mock"),
        reward_start_at=reward_start_at,
        reward_end_at=reward_end_at,
    )


class TestRewardTimePolicy:
    """Tests for RewardTimePolicy."""

    def test_reward_active_within_window(self):
        """Reward should be active when within reward time window."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=2),
            reward_start_at=now - timedelta(hours=1),
            reward_end_at=now + timedelta(hours=1),
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is True

    def test_reward_not_started(self):
        """Reward should be inactive before reward_start_at."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=4),
            reward_start_at=now + timedelta(hours=1),
            reward_end_at=now + timedelta(hours=3),
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is False
        assert "尚未开始" in reason

    def test_reward_ended(self):
        """Reward should be inactive after reward_end_at."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now - timedelta(hours=4),
            end_at=now + timedelta(hours=2),
            reward_start_at=now - timedelta(hours=3),
            reward_end_at=now - timedelta(hours=1),
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is False
        assert "已结束" in reason

    def test_no_reward_times_falls_back_to_activity(self):
        """Without reward times, reward window = activity window."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is True

    def test_no_reward_times_activity_not_started(self):
        """Without reward times, if activity not started, reward not active."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=3),
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is False

    def test_reward_window_narrower_than_activity(self):
        """Activity active but reward window already ended."""
        now = datetime.now()
        config = _make_activity_config(
            start_at=now - timedelta(hours=4),
            end_at=now + timedelta(hours=4),
            reward_start_at=now - timedelta(hours=3),
            reward_end_at=now - timedelta(hours=1),  # Already ended
        )
        is_active, reason = RewardTimePolicy.is_reward_active(config)
        assert is_active is False
        assert "已结束" in reason

"""Domain models for the activity system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class SessionState(Enum):
    """User session state for a level."""
    LOCKED = "LOCKED"           # Level not unlocked yet
    READY = "READY"             # Can submit answer
    INFLIGHT = "INFLIGHT"       # Waiting for system response
    COOLDOWN = "COOLDOWN"       # Failed, in cooldown period
    PASSED = "PASSED"           # Level completed
    FAILED_OUT = "FAILED_OUT"   # Max turns reached


class RewardType(Enum):
    """Types of rewards."""
    ALIPAY_CODE = "ALIPAY_CODE"
    JD_ECARD = "JD_ECARD"


class EventType(Enum):
    """Log event types."""
    USER_IN = "USER_IN"
    SYSTEM_OUT = "SYSTEM_OUT"
    LLM_CALL = "LLM_CALL"
    GRADE = "GRADE"
    REWARD_CLAIM = "REWARD_CLAIM"


class GradeVerdict(Enum):
    """Grading verdict."""
    PASS = "PASS"
    FAIL = "FAIL"
    SENSITIVE = "SENSITIVE"


class ClaimResult(Enum):
    """Reward claim result."""
    SUCCESS = "SUCCESS"
    ALREADY_CLAIMED = "ALREADY_CLAIMED"
    NO_STOCK = "NO_STOCK"
    POOL_DISABLED = "POOL_DISABLED"
    ERROR = "ERROR"


@dataclass
class User:
    """User model."""
    id: int
    telegram_user_id: int
    username: Optional[str]
    is_banned: bool = False
    ban_reason: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Session:
    """User session for a specific level."""
    id: int
    user_id: int
    level_id: int
    state: SessionState
    turn_count: int = 0
    last_attempt_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Attempt:
    """A single attempt at a level."""
    id: Optional[int]
    user_id: int
    level_id: int
    session_id: int
    turn_index: int
    user_prompt: str
    llm_output: Optional[str]
    keyword_verdict: Optional[GradeVerdict]
    judge_verdict: Optional[GradeVerdict]
    final_verdict: Optional[GradeVerdict]
    grade_reason: Optional[str]
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class LevelProgress:
    """User's progress on a level."""
    id: int
    user_id: int
    level_id: int
    passed: bool
    passed_at: Optional[datetime]
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RewardClaim:
    """Record of a reward claim."""
    id: Optional[int]
    user_id: int
    level_id: int
    pool_id: str
    item_id: str
    reward_code: str
    claimed_at: datetime = field(default_factory=datetime.now)


@dataclass
class LogEvent:
    """Audit log event."""
    id: Optional[int]
    trace_id: str
    event_type: EventType
    telegram_user_id: int
    chat_id: int
    level_id: Optional[int]
    session_id: Optional[int]
    turn_index: Optional[int]
    content: str
    metadata: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Message:
    """Unified message object from channel."""
    user_id: int          # Telegram user ID
    chat_id: int          # Telegram chat ID
    message_id: int       # Message ID for reply
    text: str             # Message text
    username: Optional[str]
    nickname: Optional[str]
    timestamp: datetime


@dataclass
class LLMResult:
    """Result from LLM generation."""
    output: str
    model: str
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


@dataclass
class JudgeResult:
    """Result from LLM judge."""
    verdict: GradeVerdict
    reason: str
    raw_output: str
    error: Optional[str] = None


@dataclass
class GradeResult:
    """Combined grading result."""
    keyword_verdict: GradeVerdict
    judge_verdict: GradeVerdict
    final_verdict: GradeVerdict
    keyword_reason: str
    judge_reason: str
    judge_parse_error: bool = False
    judge_error: bool = False


@dataclass 
class RewardClaimResponse:
    """Response from claiming a reward."""
    result: ClaimResult
    reward_code: Optional[str] = None
    item_id: Optional[str] = None
    message: Optional[str] = None


@dataclass
class TaskPayload:
    """Payload for a queued task."""
    trace_id: str
    user_id: int
    telegram_user_id: int
    chat_id: int
    message_id: int
    username: Optional[str]
    level_id: int
    session_id: int
    user_prompt: str
    turn_index: int
    enqueued_at: datetime = field(default_factory=datetime.now)

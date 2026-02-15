"""Content provider interface for loading activity configuration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class LevelLimits:
    """Limits configuration for a level."""
    max_input_chars: int
    max_turns: int
    cooldown_seconds_after_fail: int
    max_output_tokens: int


@dataclass
class KeywordGradingConfig:
    """Keyword grading configuration."""
    target_phrase: str
    match_policy: str = "substring"  # exact, substring, regex, case_insensitive


@dataclass
class JudgeGradingConfig:
    """Judge grading configuration."""
    enabled: bool = True
    judge_model: Optional[str] = None
    policy: str = "pass_if_intended_and_not_refusal"


@dataclass
class GradingConfig:
    """Combined grading configuration."""
    keyword: KeywordGradingConfig
    judge: JudgeGradingConfig


@dataclass
class PromptConfig:
    """Prompt configuration for a level."""
    system_prompt: str
    intro_message: str


@dataclass
class LevelConfig:
    """Configuration for a single level."""
    level_id: int
    name: str
    enabled: bool
    prompt: PromptConfig
    limits: LevelLimits
    grading: GradingConfig
    reward_pool_id: str
    unlock_policy: Dict[str, str] = field(default_factory=lambda: {"type": "sequential"})
    generate_model: Optional[str] = None  # Per-level generation model override


@dataclass
class RewardItem:
    """A single reward item."""
    item_id: str
    type: str  # ALIPAY_CODE or JD_ECARD
    code: str
    max_claims_per_item: int


@dataclass
class RewardPoolConfig:
    """Configuration for a reward pool."""
    pool_id: str
    name: str
    enabled: bool
    send_message_template: str
    items: List[RewardItem]


@dataclass
class GlobalLimits:
    """Global activity limits."""
    max_inflight_per_user: int = 1
    queue_max_length: int = 20000
    worker_concurrency: int = 8


@dataclass
class LLMConfig:
    """LLM configuration."""
    provider: str
    model: str
    timeout_seconds: int = 30
    default_max_output_tokens: int = 256


@dataclass
class ChannelConfig:
    """Channel configuration."""
    name: str
    bot_display_name: str


@dataclass
class ActivityConfig:
    """Main activity configuration."""
    activity_id: str
    title: str
    enabled: bool
    start_at: datetime
    end_at: datetime
    channel: ChannelConfig
    global_limits: GlobalLimits
    llm: LLMConfig
    timezone: str = "Asia/Shanghai"
    judge_timeout_strategy: str = "fail_no_count"  # fail_count, fail_no_count, retry
    reward_start_at: Optional[datetime] = None  # Reward window start (None = same as start_at)
    reward_end_at: Optional[datetime] = None    # Reward window end (None = same as end_at)


class IContentProvider(ABC):
    """Interface for loading activity content from configuration."""
    
    @abstractmethod
    def load_activity(self) -> ActivityConfig:
        """Load activity configuration.
        
        Returns:
            ActivityConfig object
        """
        pass
    
    @abstractmethod
    def load_levels(self) -> List[LevelConfig]:
        """Load all level configurations.
        
        Returns:
            List of LevelConfig objects
        """
        pass
    
    @abstractmethod
    def load_rewards(self) -> List[RewardPoolConfig]:
        """Load all reward pool configurations.
        
        Returns:
            List of RewardPoolConfig objects
        """
        pass
    
    @abstractmethod
    def reload(self) -> None:
        """Reload all configurations from source."""
        pass
    
    @abstractmethod
    def get_level(self, level_id: int) -> Optional[LevelConfig]:
        """Get a specific level configuration.
        
        Args:
            level_id: Level ID
            
        Returns:
            LevelConfig or None
        """
        pass
    
    @abstractmethod
    def get_reward_pool(self, pool_id: str) -> Optional[RewardPoolConfig]:
        """Get a specific reward pool configuration.
        
        Args:
            pool_id: Pool ID
            
        Returns:
            RewardPoolConfig or None
        """
        pass
    
    @abstractmethod
    def validate(self) -> List[str]:
        """Validate all configurations.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        pass

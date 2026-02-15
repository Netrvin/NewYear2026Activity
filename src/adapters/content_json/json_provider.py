"""JSON-based content provider implementation."""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

from ...ports.content import (
    IContentProvider,
    ActivityConfig,
    LevelConfig,
    RewardPoolConfig,
    LevelLimits,
    KeywordGradingConfig,
    JudgeGradingConfig,
    GradingConfig,
    PromptConfig,
    RewardItem,
    GlobalLimits,
    LLMConfig,
    ChannelConfig,
)


class JsonContentProvider(IContentProvider):
    """Load activity content from JSON files."""
    
    def __init__(
        self,
        activity_path: Path,
        levels_path: Path,
        rewards_path: Path
    ):
        self.activity_path = activity_path
        self.levels_path = levels_path
        self.rewards_path = rewards_path
        
        self._activity: Optional[ActivityConfig] = None
        self._levels: Optional[List[LevelConfig]] = None
        self._rewards: Optional[List[RewardPoolConfig]] = None
        self._levels_map: Dict[int, LevelConfig] = {}
        self._rewards_map: Dict[str, RewardPoolConfig] = {}
        
        # Load on init
        self.reload()
    
    def load_activity(self) -> ActivityConfig:
        """Load activity configuration."""
        if self._activity is None:
            self._load_activity_config()
        return self._activity
    
    def load_levels(self) -> List[LevelConfig]:
        """Load all level configurations."""
        if self._levels is None:
            self._load_levels_config()
        return self._levels
    
    def load_rewards(self) -> List[RewardPoolConfig]:
        """Load all reward pool configurations."""
        if self._rewards is None:
            self._load_rewards_config()
        return self._rewards
    
    def reload(self) -> None:
        """Reload all configurations from files."""
        self._activity = None
        self._levels = None
        self._rewards = None
        self._levels_map = {}
        self._rewards_map = {}
        
        self._load_activity_config()
        self._load_levels_config()
        self._load_rewards_config()
    
    def get_level(self, level_id: int) -> Optional[LevelConfig]:
        """Get a specific level configuration."""
        if not self._levels_map:
            self.load_levels()
        return self._levels_map.get(level_id)
    
    def get_reward_pool(self, pool_id: str) -> Optional[RewardPoolConfig]:
        """Get a specific reward pool configuration."""
        if not self._rewards_map:
            self.load_rewards()
        return self._rewards_map.get(pool_id)
    
    def validate(self) -> List[str]:
        """Validate all configurations."""
        errors = []
        
        try:
            activity = self.load_activity()
            levels = self.load_levels()
            rewards = self.load_rewards()
        except Exception as e:
            errors.append(f"Failed to load configurations: {str(e)}")
            return errors
        
        # Check level_id continuity
        level_ids = [l.level_id for l in levels]
        expected_ids = list(range(1, len(levels) + 1))
        if sorted(level_ids) != expected_ids:
            errors.append(f"Level IDs must be continuous from 1: got {level_ids}")
        
        # Check reward_pool_id exists
        reward_pool_ids = {r.pool_id for r in rewards}
        for level in levels:
            if level.reward_pool_id not in reward_pool_ids:
                errors.append(
                    f"Level {level.level_id} references non-existent "
                    f"reward pool: {level.reward_pool_id}"
                )
        
        # Check max_claims_per_item for JD_ECARD
        for pool in rewards:
            for item in pool.items:
                if item.type == "JD_ECARD" and item.max_claims_per_item != 1:
                    errors.append(
                        f"JD_ECARD item {item.item_id} in pool {pool.pool_id} "
                        f"must have max_claims_per_item=1, got {item.max_claims_per_item}"
                    )
        
        # Check limits fields
        for level in levels:
            limits = level.limits
            if limits.max_input_chars <= 0:
                errors.append(f"Level {level.level_id}: max_input_chars must be positive")
            if limits.max_turns <= 0:
                errors.append(f"Level {level.level_id}: max_turns must be positive")
            if limits.cooldown_seconds_after_fail < 0:
                errors.append(f"Level {level.level_id}: cooldown_seconds_after_fail must be non-negative")
            if limits.max_output_tokens <= 0:
                errors.append(f"Level {level.level_id}: max_output_tokens must be positive")
        
        return errors
    
    def _load_activity_config(self) -> None:
        """Load activity.json and parse into ActivityConfig."""
        with open(self.activity_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        channel = ChannelConfig(
            name=data['channel']['name'],
            bot_display_name=data['channel']['bot_display_name']
        )
        
        global_limits = GlobalLimits(
            max_inflight_per_user=data['global_limits'].get('max_inflight_per_user', 1),
            queue_max_length=data['global_limits'].get('queue_max_length', 20000),
            worker_concurrency=data['global_limits'].get('worker_concurrency', 8)
        )
        
        llm = LLMConfig(
            provider=data['llm']['provider'],
            model=data['llm']['model'],
            timeout_seconds=data['llm'].get('timeout_seconds', 30),
            default_max_output_tokens=data['llm'].get('default_max_output_tokens', 256)
        )
        
        # Parse optional reward time window
        reward_start_at = None
        if 'reward_start_at' in data:
            reward_start_at = datetime.fromisoformat(data['reward_start_at'])
        reward_end_at = None
        if 'reward_end_at' in data:
            reward_end_at = datetime.fromisoformat(data['reward_end_at'])

        self._activity = ActivityConfig(
            activity_id=data['activity_id'],
            title=data['title'],
            enabled=data['enabled'],
            start_at=datetime.fromisoformat(data['start_at']),
            end_at=datetime.fromisoformat(data['end_at']),
            channel=channel,
            global_limits=global_limits,
            llm=llm,
            timezone=data.get('timezone', 'Asia/Shanghai'),
            judge_timeout_strategy=data.get('judge_timeout_strategy', 'fail_no_count'),
            reward_start_at=reward_start_at,
            reward_end_at=reward_end_at
        )
    
    def _load_levels_config(self) -> None:
        """Load levels.json and parse into LevelConfig list."""
        with open(self.levels_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self._levels = []
        self._levels_map = {}
        
        for level_data in data['levels']:
            prompt = PromptConfig(
                system_prompt=level_data['prompt']['system_prompt'],
                intro_message=level_data['prompt']['intro_message']
            )
            
            limits = LevelLimits(
                max_input_chars=level_data['limits']['max_input_chars'],
                max_turns=level_data['limits']['max_turns'],
                cooldown_seconds_after_fail=level_data['limits']['cooldown_seconds_after_fail'],
                max_output_tokens=level_data['limits']['max_output_tokens']
            )
            
            keyword = KeywordGradingConfig(
                target_phrase=level_data['grading']['keyword']['target_phrase'],
                match_policy=level_data['grading']['keyword'].get('match_policy', 'substring')
            )
            
            judge = JudgeGradingConfig(
                enabled=level_data['grading']['judge'].get('enabled', True),
                judge_model=level_data['grading']['judge'].get('judge_model'),
                policy=level_data['grading']['judge'].get('policy', 'pass_if_intended_and_not_refusal')
            )
            
            grading = GradingConfig(keyword=keyword, judge=judge)
            
            level = LevelConfig(
                level_id=level_data['level_id'],
                name=level_data['name'],
                enabled=level_data['enabled'],
                prompt=prompt,
                limits=limits,
                grading=grading,
                reward_pool_id=level_data['reward_pool_id'],
                unlock_policy=level_data.get('unlock_policy', {'type': 'sequential'}),
                generate_model=level_data.get('generate_model')
            )
            
            self._levels.append(level)
            self._levels_map[level.level_id] = level
    
    def _load_rewards_config(self) -> None:
        """Load rewards.json and parse into RewardPoolConfig list."""
        with open(self.rewards_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self._rewards = []
        self._rewards_map = {}
        
        for pool_data in data['reward_pools']:
            items = [
                RewardItem(
                    item_id=item['item_id'],
                    type=item['type'],
                    code=item['code'],
                    max_claims_per_item=item['max_claims_per_item']
                )
                for item in pool_data['items']
            ]
            
            pool = RewardPoolConfig(
                pool_id=pool_data['pool_id'],
                name=pool_data['name'],
                enabled=pool_data['enabled'],
                send_message_template=pool_data['send_message_template'],
                items=items
            )
            
            self._rewards.append(pool)
            self._rewards_map[pool.pool_id] = pool

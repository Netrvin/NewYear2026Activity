"""Tests for JSON content provider."""

import pytest
import tempfile
from pathlib import Path

from ..adapters.content_json.json_provider import JsonContentProvider


class TestJsonContentProvider:
    """Tests for JSON content provider."""
    
    def test_load_activity(self, content_provider):
        """Test loading activity configuration."""
        activity = content_provider.load_activity()
        
        assert activity.activity_id == "test_activity"
        assert activity.title == "Test Activity"
        assert activity.enabled is True
        assert activity.global_limits.worker_concurrency == 2
    
    def test_load_levels(self, content_provider):
        """Test loading level configurations."""
        levels = content_provider.load_levels()
        
        assert len(levels) == 2
        assert levels[0].level_id == 1
        assert levels[0].name == "Test Level 1"
        assert levels[0].limits.max_input_chars == 100
        assert levels[0].grading.keyword.target_phrase == "TARGET-PHRASE-1"
    
    def test_load_rewards(self, content_provider):
        """Test loading reward pool configurations."""
        rewards = content_provider.load_rewards()
        
        assert len(rewards) == 2
        assert rewards[0].pool_id == "pool_1"
        assert len(rewards[0].items) == 1
        assert rewards[0].items[0].type == "ALIPAY_CODE"
    
    def test_get_level(self, content_provider):
        """Test getting specific level."""
        level = content_provider.get_level(1)
        assert level is not None
        assert level.level_id == 1
        
        # Non-existent level
        level = content_provider.get_level(99)
        assert level is None
    
    def test_get_reward_pool(self, content_provider):
        """Test getting specific reward pool."""
        pool = content_provider.get_reward_pool("pool_1")
        assert pool is not None
        assert pool.pool_id == "pool_1"
        
        # Non-existent pool
        pool = content_provider.get_reward_pool("nonexistent")
        assert pool is None
    
    def test_validate_success(self, content_provider):
        """Test validation passes for valid config."""
        errors = content_provider.validate()
        assert len(errors) == 0
    
    def test_validate_invalid_jd_ecard(self, temp_dir):
        """Test validation fails for invalid JD E-card config."""
        config_dir = temp_dir / "config"
        config_dir.mkdir()
        
        # Valid activity and levels
        (config_dir / "activity.json").write_text('''
        {
            "activity_id": "test", "title": "Test", "enabled": true,
            "start_at": "2026-01-01T00:00:00+08:00",
            "end_at": "2026-12-31T00:00:00+08:00",
            "channel": {"name": "test", "bot_display_name": "Test"},
            "global_limits": {"max_inflight_per_user": 1, "queue_max_length": 100, "worker_concurrency": 1},
            "llm": {"provider": "test", "model": "test"}
        }
        ''', encoding='utf-8')
        
        (config_dir / "levels.json").write_text('''
        {"levels": [
            {
                "level_id": 1, "name": "L1", "enabled": true,
                "prompt": {"system_prompt": "test", "intro_message": "test"},
                "limits": {"max_input_chars": 100, "max_turns": 3, "cooldown_seconds_after_fail": 5, "max_output_tokens": 50},
                "grading": {"keyword": {"target_phrase": "X"}, "judge": {"enabled": false}},
                "reward_pool_id": "pool_1"
            }
        ]}
        ''', encoding='utf-8')
        
        # Invalid: JD E-card with max_claims_per_item > 1
        (config_dir / "rewards.json").write_text('''
        {"reward_pools": [
            {
                "pool_id": "pool_1", "name": "Test", "enabled": true,
                "send_message_template": "{reward_code}",
                "items": [{"item_id": "i1", "type": "JD_ECARD", "code": "X", "max_claims_per_item": 5}]
            }
        ]}
        ''', encoding='utf-8')
        
        provider = JsonContentProvider(
            activity_path=config_dir / "activity.json",
            levels_path=config_dir / "levels.json",
            rewards_path=config_dir / "rewards.json"
        )
        
        errors = provider.validate()
        assert len(errors) > 0
        assert any("JD_ECARD" in e for e in errors)
    
    def test_reload(self, content_provider, config_dir):
        """Test configuration reload."""
        # Initial load
        levels = content_provider.load_levels()
        assert len(levels) == 2
        
        # Modify config
        new_levels = '''{"levels": [
            {
                "level_id": 1, "name": "Modified Level", "enabled": true,
                "prompt": {"system_prompt": "test", "intro_message": "test"},
                "limits": {"max_input_chars": 100, "max_turns": 3, "cooldown_seconds_after_fail": 5, "max_output_tokens": 50},
                "grading": {"keyword": {"target_phrase": "X"}, "judge": {"enabled": false}},
                "reward_pool_id": "pool_1"
            }
        ]}'''
        (config_dir / "levels.json").write_text(new_levels, encoding='utf-8')
        
        # Reload
        content_provider.reload()
        
        # Check new config is loaded
        levels = content_provider.load_levels()
        assert len(levels) == 1
        assert levels[0].name == "Modified Level"


class TestPerLevelModelConfig:
    """Tests for per-level model configuration."""

    def test_generate_model_loaded(self, content_provider):
        """Level with generate_model should have it set."""
        level = content_provider.get_level(1)
        assert level.generate_model == "gen-model-lv1"

    def test_generate_model_default_none(self, content_provider):
        """Level without generate_model should default to None."""
        level = content_provider.get_level(2)
        assert level.generate_model is None

    def test_judge_model_loaded(self, content_provider):
        """Level with judge_model should have it set."""
        level = content_provider.get_level(1)
        assert level.grading.judge.judge_model == "judge-model-lv1"

    def test_judge_model_default_none(self, content_provider):
        """Level without judge_model should default to None."""
        level = content_provider.get_level(2)
        assert level.grading.judge.judge_model is None


class TestRewardTimeConfig:
    """Tests for reward time window configuration."""

    def test_no_reward_times_defaults_to_none(self, content_provider):
        """Activity without reward times should have None."""
        activity = content_provider.load_activity()
        assert activity.reward_start_at is None
        assert activity.reward_end_at is None

    def test_reward_times_parsed(self, temp_dir):
        """Activity with reward times should have them parsed."""
        config_dir = temp_dir / "config"
        config_dir.mkdir()

        (config_dir / "activity.json").write_text('''{
            "activity_id": "test", "title": "Test", "enabled": true,
            "start_at": "2026-01-01T00:00:00+08:00",
            "end_at": "2026-12-31T23:59:59+08:00",
            "reward_start_at": "2026-01-01T10:00:00+08:00",
            "reward_end_at": "2026-06-30T18:00:00+08:00",
            "channel": {"name": "test", "bot_display_name": "Test"},
            "global_limits": {"max_inflight_per_user": 1, "queue_max_length": 100, "worker_concurrency": 1},
            "llm": {"provider": "test", "model": "test"}
        }''', encoding='utf-8')

        (config_dir / "levels.json").write_text('''{"levels": [
            {
                "level_id": 1, "name": "L1", "enabled": true,
                "prompt": {"system_prompt": "test", "intro_message": "test"},
                "limits": {"max_input_chars": 100, "max_turns": 3, "cooldown_seconds_after_fail": 5, "max_output_tokens": 50},
                "grading": {"keyword": {"target_phrase": "X"}, "judge": {"enabled": false}},
                "reward_pool_id": "pool_1"
            }
        ]}''', encoding='utf-8')

        (config_dir / "rewards.json").write_text('''{"reward_pools": [
            {
                "pool_id": "pool_1", "name": "Test", "enabled": true,
                "send_message_template": "{reward_code}",
                "items": [{"item_id": "i1", "type": "ALIPAY_CODE", "code": "X", "max_claims_per_item": 999}]
            }
        ]}''', encoding='utf-8')

        provider = JsonContentProvider(
            activity_path=config_dir / "activity.json",
            levels_path=config_dir / "levels.json",
            rewards_path=config_dir / "rewards.json"
        )

        activity = provider.load_activity()
        assert activity.reward_start_at is not None
        assert activity.reward_end_at is not None
        assert activity.reward_start_at.hour == 10
        assert activity.reward_end_at.month == 6

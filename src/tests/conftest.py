"""Test fixtures and configuration."""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from ..adapters.storage_sqlite.sqlite_storage import SQLiteStorage
from ..adapters.content_json.json_provider import JsonContentProvider
from ..adapters.llm_openai_compat.client import MockLLMClient
from ..adapters.queue_memory.memory_queue import MemoryQueue
from ..domain.models import GradeVerdict


# Use scope="session" for event_loop_policy to avoid deprecation warning
@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy."""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import gc
    import time
    
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)
    
    # Force garbage collection to release file handles on Windows
    gc.collect()
    time.sleep(0.1)


@pytest.fixture
def config_dir(temp_dir):
    """Create config files in temp directory."""
    config = temp_dir / "config"
    config.mkdir()
    
    # activity.json
    activity_json = '''{
    "activity_id": "test_activity",
    "title": "Test Activity",
    "enabled": true,
    "start_at": "2026-01-01T00:00:00+08:00",
    "end_at": "2026-12-31T23:59:59+08:00",
    "timezone": "Asia/Shanghai",
    "channel": {"name": "telegram", "bot_display_name": "TestBot"},
    "global_limits": {
        "max_inflight_per_user": 1,
        "queue_max_length": 1000,
        "worker_concurrency": 2
    },
    "llm": {
        "provider": "mock",
        "model": "mock-model",
        "timeout_seconds": 10,
        "default_max_output_tokens": 100
    },
    "judge_timeout_strategy": "fail_no_count"
}'''
    (config / "activity.json").write_text(activity_json, encoding='utf-8')
    
    _write_levels_json(config)
    _write_rewards_json(config)
    
    return config


@pytest.fixture
def future_config_dir(temp_dir):
    """Create config files with a future start_at (activity not yet started)."""
    config = temp_dir / "config"
    config.mkdir(exist_ok=True)
    
    activity_json = '''{
    "activity_id": "test_activity",
    "title": "Test Activity",
    "enabled": true,
    "start_at": "2099-01-01T00:00:00+08:00",
    "end_at": "2099-12-31T23:59:59+08:00",
    "timezone": "Asia/Shanghai",
    "channel": {"name": "telegram", "bot_display_name": "TestBot"},
    "global_limits": {
        "max_inflight_per_user": 1,
        "queue_max_length": 1000,
        "worker_concurrency": 2
    },
    "llm": {
        "provider": "mock",
        "model": "mock-model",
        "timeout_seconds": 10,
        "default_max_output_tokens": 100
    },
    "judge_timeout_strategy": "fail_no_count"
}'''
    (config / "activity.json").write_text(activity_json, encoding='utf-8')
    
    _write_levels_json(config)
    _write_rewards_json(config)
    
    return config


@pytest.fixture
def future_content_provider(future_config_dir):
    """Create a content provider with future activity start time."""
    return JsonContentProvider(
        activity_path=future_config_dir / "activity.json",
        levels_path=future_config_dir / "levels.json",
        rewards_path=future_config_dir / "rewards.json"
    )


def _write_levels_json(config):
    """Write levels.json to config directory."""
    levels_json = '''{
    "levels": [
        {
            "level_id": 1,
            "name": "Test Level 1",
            "enabled": true,
            "unlock_policy": {"type": "sequential"},
            "generate_model": "gen-model-lv1",
            "prompt": {
                "system_prompt": "You are a test assistant.",
                "intro_message": "Level 1: Say TARGET-PHRASE-1"
            },
            "limits": {
                "max_input_chars": 100,
                "max_turns": 3,
                "cooldown_seconds_after_fail": 2,
                "max_output_tokens": 50
            },
            "grading": {
                "keyword": {"target_phrase": "TARGET-PHRASE-1", "match_policy": "substring"},
                "judge": {"enabled": true, "judge_model": "judge-model-lv1", "policy": "pass_if_intended_and_not_refusal"}
            },
            "reward_pool_id": "pool_1"
        },
        {
            "level_id": 2,
            "name": "Test Level 2",
            "enabled": true,
            "unlock_policy": {"type": "sequential"},
            "prompt": {
                "system_prompt": "You are a test assistant.",
                "intro_message": "Level 2: Say TARGET-PHRASE-2"
            },
            "limits": {
                "max_input_chars": 100,
                "max_turns": 3,
                "cooldown_seconds_after_fail": 2,
                "max_output_tokens": 50
            },
            "grading": {
                "keyword": {"target_phrase": "TARGET-PHRASE-2", "match_policy": "substring"},
                "judge": {"enabled": true, "policy": "pass_if_intended_and_not_refusal"}
            },
            "reward_pool_id": "pool_2"
        }
    ]
}'''
    (config / "levels.json").write_text(levels_json, encoding='utf-8')


def _write_rewards_json(config):
    """Write rewards.json to config directory."""
    rewards_json = '''{
    "reward_pools": [
        {
            "pool_id": "pool_1",
            "name": "Test Pool 1",
            "enabled": true,
            "send_message_template": "Reward: {reward_code}",
            "items": [
                {"item_id": "item_1_1", "type": "ALIPAY_CODE", "code": "CODE-1-1", "max_claims_per_item": 999}
            ]
        },
        {
            "pool_id": "pool_2",
            "name": "Test Pool 2",
            "enabled": true,
            "send_message_template": "Reward: {reward_code}",
            "items": [
                {"item_id": "item_2_1", "type": "JD_ECARD", "code": "ECARD-1", "max_claims_per_item": 1},
                {"item_id": "item_2_2", "type": "JD_ECARD", "code": "ECARD-2", "max_claims_per_item": 1}
            ]
        }
    ]
}'''
    (config / "rewards.json").write_text(rewards_json, encoding='utf-8')


@pytest.fixture
async def storage(temp_dir):
    """Create a test storage instance."""
    db_path = temp_dir / "test.db"
    store = SQLiteStorage(db_path)
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def content_provider(config_dir):
    """Create a test content provider."""
    return JsonContentProvider(
        activity_path=config_dir / "activity.json",
        levels_path=config_dir / "levels.json",
        rewards_path=config_dir / "rewards.json"
    )


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    client = MockLLMClient()
    # Set up default responses
    client.set_default_generate_output("Here is some output")
    client.set_default_judge_verdict(GradeVerdict.PASS)
    return client


@pytest.fixture
def memory_queue():
    """Create a memory queue."""
    return MemoryQueue(max_size=100)

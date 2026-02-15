"""Integration tests for end-to-end flows."""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from ..domain.engine import GameEngine
from ..domain.models import Message, GradeVerdict, SessionState
from ..adapters.llm_openai_compat.client import MockLLMClient
from ..adapters.queue_memory.memory_queue import MemoryQueue


class MockChannel:
    """Mock channel for testing."""
    
    def __init__(self):
        self.sent_messages = []
        self.reply_messages = []
    
    async def send_text(self, chat_id: int, text: str) -> None:
        self.sent_messages.append({'chat_id': chat_id, 'text': text})
    
    async def reply_to(self, chat_id: int, reply_to_message_id: int, text: str) -> None:
        self.reply_messages.append({
            'chat_id': chat_id,
            'reply_to': reply_to_message_id,
            'text': text
        })
    
    async def start(self) -> None:
        pass
    
    async def stop(self) -> None:
        pass
    
    def set_message_handler(self, handler) -> None:
        pass
    
    def set_command_handler(self, command: str, handler) -> None:
        pass


class TestEndToEndFlow:
    """End-to-end integration tests."""
    
    @pytest.fixture
    async def engine(self, storage, content_provider, mock_llm):
        """Create game engine with all dependencies."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        # Sync reward items
        rewards = content_provider.load_rewards()
        await storage.sync_reward_items([
            {
                'pool_id': r.pool_id,
                'items': [
                    {'item_id': i.item_id, 'type': i.type, 'code': i.code, 'max_claims_per_item': i.max_claims_per_item}
                    for i in r.items
                ]
            }
            for r in rewards
        ])
        
        return engine
    
    @pytest.mark.asyncio
    async def test_start_command(self, engine):
        """Test /start command."""
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="/start",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_start(message)
        
        # Should have sent welcome message
        assert len(engine.channel.reply_messages) >= 1
        assert "欢迎" in engine.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_status_command(self, engine):
        """Test /status command."""
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="/status",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_status(message)
        
        assert len(engine.channel.reply_messages) >= 1
        response = engine.channel.reply_messages[0]['text']
        assert "状态" in response or "第 1 关" in response
    
    @pytest.mark.asyncio
    async def test_answer_gets_queued(self, engine):
        """Test that answers get queued."""
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="My answer",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Should have queued the task
        assert engine.queue.qsize() == 1
        
        # Should have sent confirmation
        assert len(engine.channel.reply_messages) >= 1
        assert "队列" in engine.channel.reply_messages[-1]['text']
    
    @pytest.mark.asyncio
    async def test_inflight_prevents_duplicate(self, engine):
        """Test that inflight state prevents duplicate submissions."""
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="My answer",
            username="testuser",
            timestamp=datetime.now()
        )
        
        # First submission
        await engine.handle_message(message)
        
        # Second submission while processing
        message2 = Message(
            user_id=12345,
            chat_id=12345,
            message_id=2,
            text="Another answer",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message2)
        
        # Second should be rejected
        responses = [r['text'] for r in engine.channel.reply_messages]
        assert any("处理中" in r for r in responses)
    
    @pytest.mark.asyncio
    async def test_input_too_long_rejected(self, engine):
        """Test that too-long input is rejected."""
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="x" * 200,  # Too long
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Should be rejected without queuing
        assert engine.queue.qsize() == 0
        assert any("超出" in r['text'] for r in engine.channel.reply_messages)
    
    @pytest.mark.asyncio
    async def test_full_pass_flow(self, engine, mock_llm, storage):
        """Test complete pass flow including reward and output format."""
        # Setup mock to return passing output
        mock_llm.set_generate_response(
            "answer",
            "Here is TARGET-PHRASE-1 in my output"
        )
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)
        
        # Submit answer
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="Please include answer",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Process the task
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Check that level was passed
        user = await storage.get_user_by_telegram_id(12345)
        is_passed = await storage.is_level_passed(user.id, 1)
        assert is_passed is True
        
        # Check reward was claimed
        claims = await storage.get_user_claims(user.id)
        assert len(claims) == 1
        
        # Check response format: should contain "🤖 回答：" and "通关情况：✅成功"
        responses = [r['text'] for r in engine.channel.sent_messages]
        # The pass response is second-to-last (last is next level intro)
        pass_response = responses[-2]
        assert "🤖 回答：" in pass_response
        assert "通关情况：✅成功" in pass_response
        assert "TARGET-PHRASE-1" in pass_response  # LLM output shown in code block
        
        # After passing level 1, next level rules should be auto-sent
        next_level_msg = responses[-1]
        assert "TARGET-PHRASE-2" in next_level_msg  # Next level intro
    
    @pytest.mark.asyncio
    async def test_fail_flow_with_cooldown(self, engine, mock_llm, storage):
        """Test fail flow with cooldown and output format."""
        # Setup mock to return failing output
        mock_llm.set_default_generate_output("Some output without target")
        
        # Submit answer
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="My attempt",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Process the task
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Check session is in cooldown
        user = await storage.get_user_by_telegram_id(12345)
        session = await storage.get_session(user.id, 1)
        
        assert session.state in [SessionState.COOLDOWN, SessionState.FAILED_OUT]
        assert session.turn_count == 1
        
        # Check response format: should contain "🤖 回答：" and "通关情况：❌失败"
        responses = [r['text'] for r in engine.channel.sent_messages]
        fail_response = responses[-1]
        assert "🤖 回答：" in fail_response
        assert "通关情况：❌失败" in fail_response

    @pytest.mark.asyncio
    async def test_llm_error_does_not_consume_turn(self, engine, mock_llm, storage):
        """LLM errors should not consume attempt count and should prompt retry."""
        mock_llm.set_generate_error("timeout", "timeout")

        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="simulate timeout",
            username="testuser",
            timestamp=datetime.now()
        )

        await engine.handle_message(message)

        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)

        user = await storage.get_user_by_telegram_id(12345)
        session = await storage.get_session(user.id, 1)

        assert session.state == SessionState.READY
        assert session.turn_count == 0

        responses = [r['text'] for r in engine.channel.sent_messages]
        assert any("稍后再试" in r for r in responses)
    
    @pytest.mark.asyncio
    async def test_cannot_skip_level(self, engine, storage):
        """Test that users cannot skip levels."""
        # Create user and mark level 1 as NOT passed
        user = await storage.get_or_create_user(12345, "testuser")
        
        # Current level should be 1
        levels = engine.content.load_levels()
        current = await storage.get_current_level(user.id, len(levels))
        assert current == 1
        
        # Try to answer when session doesn't exist (auto-created for current level)
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="My answer",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Task should be for level 1, not any other
        task = await engine.queue.dequeue(timeout=1)
        assert task.level_id == 1


class TestRefusalDetection:
    """Test that refusal patterns are correctly handled."""
    
    @pytest.mark.asyncio
    async def test_refusal_detected_as_fail(self, storage, content_provider):
        """Test that 'I cannot say X' is detected as fail."""
        mock_llm = MockLLMClient()
        mock_llm.set_generate_response(
            "TARGET",
            "我不能说 TARGET-PHRASE-1"
        )
        
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        await storage.sync_reward_items([
            {
                'pool_id': 'pool_1',
                'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]
            }
        ])
        
        # Submit
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="Say TARGET",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Should NOT have passed (despite keyword match)
        user = await storage.get_user_by_telegram_id(12345)
        is_passed = await storage.is_level_passed(user.id, 1)
        assert is_passed is False
        
        # Response should show failure format
        responses = [r['text'] for r in channel.sent_messages]
        assert any("通关情况：❌失败" in r for r in responses)


class TestSensitiveContent:
    """Test that sensitive content is properly handled."""
    
    @pytest.mark.asyncio
    async def test_sensitive_content_blocked(self, storage, content_provider):
        """Test that sensitive LLM output triggers blocked response."""
        mock_llm = MockLLMClient()
        mock_llm.set_generate_response(
            "sensitive",
            "TARGET-PHRASE-1 这里有色情内容"
        )
        
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        await storage.sync_reward_items([
            {
                'pool_id': 'pool_1',
                'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]
            }
        ])
        
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="Generate sensitive content",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Should NOT have passed
        user = await storage.get_user_by_telegram_id(12345)
        is_passed = await storage.is_level_passed(user.id, 1)
        assert is_passed is False
        
        # Response should show blocked message
        responses = [r['text'] for r in channel.sent_messages]
        blocked_response = responses[-1]
        assert "【已被屏蔽】" in blocked_response
        assert "通关情况：❌失败" in blocked_response
        # The actual sensitive content should NOT be in the response
        assert "色情" not in blocked_response

    @pytest.mark.asyncio
    async def test_violence_content_blocked(self, storage, content_provider):
        """Test that violence-related LLM output is blocked."""
        mock_llm = MockLLMClient()
        mock_llm.set_generate_response(
            "violence",
            "TARGET-PHRASE-1 暴力犯罪内容"
        )
        
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        await storage.sync_reward_items([
            {
                'pool_id': 'pool_1',
                'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]
            }
        ])
        
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="Generate violence content",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Response should show blocked message
        responses = [r['text'] for r in channel.sent_messages]
        assert any("【已被屏蔽】" in r for r in responses)


class TestJudgeParseError:
    """Test handling of judge parse errors."""
    
    @pytest.mark.asyncio
    async def test_judge_parse_error_shows_system_failure(self, storage, content_provider):
        """Test that unrecognizable judge verdict shows system failure message."""
        mock_llm = MockLLMClient()
        
        # Mock generate to return output with keyword
        mock_llm.set_generate_response(
            "parse_error",
            "Output: TARGET-PHRASE-1"
        )
        # Set judge to return an error (simulating parse failure)
        mock_llm.set_judge_response(
            "TARGET-PHRASE-1",
            GradeVerdict.FAIL,
            "parse error simulation"
        )
        
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        await storage.sync_reward_items([
            {
                'pool_id': 'pool_1',
                'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]
            }
        ])
        
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="Test parse_error case",
            username="testuser",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)
        
        # Should NOT have passed
        user = await storage.get_user_by_telegram_id(12345)
        is_passed = await storage.is_level_passed(user.id, 1)
        assert is_passed is False


class TestBannedUserFlow:
    """Test banned user cannot play."""
    
    @pytest.mark.asyncio
    async def test_banned_user_rejected(self, storage, content_provider):
        """Test that banned user gets rejected."""
        mock_llm = MockLLMClient()
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        
        # Create and ban user
        await storage.get_or_create_user(12345, "banned_user")
        await storage.update_user_ban_status(12345, True, "cheating")
        
        message = Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text="My answer",
            username="banned_user",
            timestamp=datetime.now()
        )
        
        await engine.handle_message(message)
        
        # Should be rejected without queuing
        assert queue.qsize() == 0
        responses = [r['text'] for r in channel.reply_messages]
        assert any("封禁" in r for r in responses)


class TestActivityNotStarted:
    """Test that all interactions are blocked when activity has not started."""

    @pytest.fixture
    async def engine_not_started(self, storage, future_content_provider, mock_llm):
        """Create game engine with activity that hasn't started yet."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        engine = GameEngine(
            storage=storage,
            content=future_content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )
        return engine

    def _make_message(self, text: str = "hello") -> Message:
        return Message(
            user_id=12345,
            chat_id=12345,
            message_id=1,
            text=text,
            username="testuser",
            timestamp=datetime.now()
        )

    @pytest.mark.asyncio
    async def test_message_blocked_before_start(self, engine_not_started):
        """Regular messages should be blocked when activity hasn't started."""
        await engine_not_started.handle_message(self._make_message("My answer"))

        assert engine_not_started.queue.qsize() == 0
        responses = [r['text'] for r in engine_not_started.channel.reply_messages]
        assert len(responses) >= 1
        assert any("活动尚未开始" in r for r in responses)

    @pytest.mark.asyncio
    async def test_start_command_blocked_before_start(self, engine_not_started):
        """/start should be blocked and not leak info when activity hasn't started."""
        await engine_not_started.handle_start(self._make_message("/start"))

        responses = [r['text'] for r in engine_not_started.channel.reply_messages]
        assert len(responses) >= 1
        assert any("活动尚未开始" in r for r in responses)
        # Must NOT contain any game info
        assert not any("欢迎" in r for r in responses)
        assert not any("关卡" in r for r in responses)

    @pytest.mark.asyncio
    async def test_help_command_blocked_before_start(self, engine_not_started):
        """/help should be blocked when activity hasn't started."""
        await engine_not_started.handle_help(self._make_message("/help"))

        responses = [r['text'] for r in engine_not_started.channel.reply_messages]
        assert len(responses) >= 1
        assert any("活动尚未开始" in r for r in responses)
        assert not any("帮助" in r for r in responses)

    @pytest.mark.asyncio
    async def test_status_command_blocked_before_start(self, engine_not_started):
        """/status should be blocked when activity hasn't started."""
        await engine_not_started.handle_status(self._make_message("/status"))

        responses = [r['text'] for r in engine_not_started.channel.reply_messages]
        assert len(responses) >= 1
        assert any("活动尚未开始" in r for r in responses)
        assert not any("状态" in r for r in responses)
        assert not any("第 1 关" in r for r in responses)

    @pytest.mark.asyncio
    async def test_rules_command_blocked_before_start(self, engine_not_started):
        """/rules should be blocked and not leak rules when activity hasn't started."""
        await engine_not_started.handle_rules(self._make_message("/rules"))

        responses = [r['text'] for r in engine_not_started.channel.reply_messages]
        assert len(responses) >= 1
        assert any("活动尚未开始" in r for r in responses)
        assert not any("规则" in r for r in responses)
        assert not any("TARGET" in r for r in responses)


class TestToggleAffectsEngine:
    """Test that admin toggle affects engine's activity check."""

    @pytest.mark.asyncio
    async def test_engine_blocks_when_admin_toggles_off(self, storage, content_provider, mock_llm):
        """Engine should block messages when admin toggles activity off."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        # Create admin and toggle off
        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        admin._toggle_override = False

        # Create engine with admin's activity status
        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status
        )

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="My answer", username="testuser", timestamp=datetime.now()
        )

        await engine.handle_message(msg)

        assert queue.qsize() == 0
        responses = [r['text'] for r in channel.reply_messages]
        assert any("关闭" in r or "强制关闭" in r for r in responses)

    @pytest.mark.asyncio
    async def test_engine_allows_when_admin_toggles_on(self, storage, content_provider, mock_llm):
        """Engine should allow messages when admin toggles activity on."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        # Admin explicitly enables
        admin._toggle_override = True

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status
        )

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="My answer", username="testuser", timestamp=datetime.now()
        )

        await engine.handle_message(msg)

        assert queue.qsize() == 1  # Message got through

    @pytest.mark.asyncio
    async def test_toggle_on_bypasses_time_check(self, storage, future_content_provider, mock_llm):
        """When admin toggles on, even future-timed activity should work."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=future_content_provider,
            channel=channel,
            queue=queue,
        )
        # Override: force enable
        admin._toggle_override = True

        engine = GameEngine(
            storage=storage,
            content=future_content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status
        )

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="My answer", username="testuser", timestamp=datetime.now()
        )

        await engine.handle_message(msg)

        # Should be allowed even though time hasn't started
        assert queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_commands_also_respect_toggle(self, storage, content_provider, mock_llm):
        """Commands like /start should also respect admin toggle."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        admin._toggle_override = False

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status
        )

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="/start", username="testuser", timestamp=datetime.now()
        )

        await engine.handle_start(msg)

        responses = [r['text'] for r in channel.reply_messages]
        assert any("关闭" in r or "强制关闭" in r for r in responses)
        # Should NOT contain welcome message
        assert not any("欢迎" in r for r in responses)


class TestPerLevelModelInEngine:
    """Test that per-level models are passed to LLM client during processing."""

    @pytest.mark.asyncio
    async def test_generate_model_passed_to_llm(self, storage, content_provider, mock_llm):
        """Engine should pass level's generate_model to LLM client."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        # Track model passed to generate
        called_generate_models = []
        original_generate = mock_llm.generate

        async def tracking_generate(system_prompt, user_prompt, max_output_tokens, model=None):
            called_generate_models.append(model)
            return await original_generate(system_prompt, user_prompt, max_output_tokens, model)

        mock_llm.generate = tracking_generate
        mock_llm.set_default_generate_output("Here is TARGET-PHRASE-1")

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )

        await storage.sync_reward_items([
            {'pool_id': 'pool_1', 'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]}
        ])

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="test answer", username="testuser", timestamp=datetime.now()
        )

        await engine.handle_message(msg)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)

        # Level 1 has generate_model="gen-model-lv1"
        assert called_generate_models == ["gen-model-lv1"]

    @pytest.mark.asyncio
    async def test_no_generate_model_passes_none(self, storage, content_provider, mock_llm):
        """When level has no generate_model, None should be passed to LLM."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        called_generate_models = []
        original_generate = mock_llm.generate

        async def tracking_generate(system_prompt, user_prompt, max_output_tokens, model=None):
            called_generate_models.append(model)
            return await original_generate(system_prompt, user_prompt, max_output_tokens, model)

        mock_llm.generate = tracking_generate
        mock_llm.set_generate_response("lv2", "Here is TARGET-PHRASE-2")

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue
        )

        await storage.sync_reward_items([
            {'pool_id': 'pool_1', 'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]},
            {'pool_id': 'pool_2', 'items': [{'item_id': 'i2', 'type': 'JD_ECARD', 'code': 'E1', 'max_claims_per_item': 1}]}
        ])

        # Pass level 1 first
        mock_llm.set_generate_response("pass1", "TARGET-PHRASE-1")
        msg1 = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="pass1", username="testuser", timestamp=datetime.now()
        )
        await engine.handle_message(msg1)
        task1 = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task1)

        called_generate_models.clear()

        # Now attempt level 2
        msg2 = Message(
            user_id=12345, chat_id=12345, message_id=2,
            text="test lv2 answer", username="testuser", timestamp=datetime.now()
        )
        await engine.handle_message(msg2)
        task2 = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task2)

        # Level 2 has no generate_model -> None
        assert called_generate_models == [None]


class TestRewardTimeAffectsEngine:
    """Test that reward time window / toggle affects pass flow."""

    @pytest.mark.asyncio
    async def test_pass_without_reward_when_reward_disabled(self, storage, content_provider, mock_llm):
        """When reward is disabled, pass should succeed but no reward claimed."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        # Force reward off
        admin._reward_toggle_override = False

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status,
            reward_enabled_fn=admin.get_reward_status
        )

        await storage.sync_reward_items([
            {'pool_id': 'pool_1', 'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'C1', 'max_claims_per_item': 999}]},
            {'pool_id': 'pool_2', 'items': [{'item_id': 'i2', 'type': 'JD_ECARD', 'code': 'E1', 'max_claims_per_item': 1}]}
        ])

        mock_llm.set_generate_response("answer", "Here is TARGET-PHRASE-1")
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="Please include answer", username="testuser", timestamp=datetime.now()
        )
        await engine.handle_message(msg)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)

        # Level should be passed
        user = await storage.get_user_by_telegram_id(12345)
        assert await storage.is_level_passed(user.id, 1) is True

        # No reward should be claimed
        claims = await storage.get_user_claims(user.id)
        assert len(claims) == 0

        # Response should NOT mention reward-related content
        responses = [r['text'] for r in channel.sent_messages]
        pass_response = responses[-2]  # last is next level intro
        assert "通关情况：✅成功" in pass_response
        assert "Reward" not in pass_response
        assert "奖品" not in pass_response
        assert "已发完" not in pass_response

    @pytest.mark.asyncio
    async def test_pass_with_reward_when_reward_enabled(self, storage, content_provider, mock_llm):
        """When reward is enabled, pass should claim reward normally."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        # Force reward on
        admin._reward_toggle_override = True

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status,
            reward_enabled_fn=admin.get_reward_status
        )

        await storage.sync_reward_items([
            {'pool_id': 'pool_1', 'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'CODE-1-1', 'max_claims_per_item': 999}]},
            {'pool_id': 'pool_2', 'items': [{'item_id': 'i2', 'type': 'JD_ECARD', 'code': 'E1', 'max_claims_per_item': 1}]}
        ])

        mock_llm.set_generate_response("answer", "Here is TARGET-PHRASE-1")
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="Please include answer", username="testuser", timestamp=datetime.now()
        )
        await engine.handle_message(msg)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)

        # Reward should be claimed
        user = await storage.get_user_by_telegram_id(12345)
        claims = await storage.get_user_claims(user.id)
        assert len(claims) == 1

        # Response should mention reward
        responses = [r['text'] for r in channel.sent_messages]
        pass_response = responses[-2]
        assert "Reward" in pass_response or "CODE-1-1" in pass_response

    @pytest.mark.asyncio
    async def test_reward_toggle_none_uses_time(self, storage, content_provider, mock_llm):
        """When reward toggle is none, reward follows time policy (active fixture → enabled)."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        from ..admin.admin_commands import AdminCommands
        admin = AdminCommands(
            admin_user_ids=[99999],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
        # No override
        admin._reward_toggle_override = None

        engine = GameEngine(
            storage=storage,
            content=content_provider,
            llm_client=mock_llm,
            channel=channel,
            queue=queue,
            activity_enabled_fn=admin.get_activity_status,
            reward_enabled_fn=admin.get_reward_status
        )

        await storage.sync_reward_items([
            {'pool_id': 'pool_1', 'items': [{'item_id': 'i1', 'type': 'ALIPAY_CODE', 'code': 'CODE-1-1', 'max_claims_per_item': 999}]},
            {'pool_id': 'pool_2', 'items': [{'item_id': 'i2', 'type': 'JD_ECARD', 'code': 'E1', 'max_claims_per_item': 1}]}
        ])

        mock_llm.set_generate_response("answer", "Here is TARGET-PHRASE-1")
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        msg = Message(
            user_id=12345, chat_id=12345, message_id=1,
            text="Please include answer", username="testuser", timestamp=datetime.now()
        )
        await engine.handle_message(msg)
        task = await engine.queue.dequeue(timeout=1)
        await engine.process_task(task)

        # content_provider has 2026-01-01 to 2026-12-31 (active) and no reward times
        # → reward follows activity time → active → reward should be claimed
        user = await storage.get_user_by_telegram_id(12345)
        claims = await storage.get_user_claims(user.id)
        assert len(claims) == 1

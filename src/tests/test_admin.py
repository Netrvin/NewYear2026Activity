"""Tests for admin commands."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from ..admin.admin_commands import AdminCommands
from ..domain.models import Message, EventType, TaskPayload
from ..adapters.queue_memory.memory_queue import MemoryQueue
from ..workers.worker import WorkerPool


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


ADMIN_USER_ID = 99999
NON_ADMIN_USER_ID = 12345


class TestAdminAuth:
    """Tests for admin authentication."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    def test_is_admin_true(self, admin):
        assert admin.is_admin(ADMIN_USER_ID) is True
    
    def test_is_admin_false(self, admin):
        assert admin.is_admin(NON_ADMIN_USER_ID) is False
    
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, admin):
        """Non-admin users should be rejected."""
        message = Message(
            user_id=NON_ADMIN_USER_ID,
            chat_id=NON_ADMIN_USER_ID,
            message_id=1,
            text="/admin stats",
            username="nonadmin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        assert len(admin.channel.reply_messages) == 1
        assert "权限" in admin.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_admin_ping(self, admin):
        """Admin ping should work."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ping",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        assert len(admin.channel.reply_messages) == 1
        assert "健康检查" in admin.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_admin_toggle(self, admin):
        """Admin toggle should support none/on/off."""
        # Default: no override (None)
        assert admin.toggle_override is None
        # But since content_provider has active time window, effective = True
        assert admin.is_activity_enabled() is True
        
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin toggle off",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert admin.toggle_override is False
        assert admin.is_activity_enabled() is False
        
        message.text = "/admin toggle on"
        await admin.handle_admin_command(message)
        assert admin.toggle_override is True
        assert admin.is_activity_enabled() is True
        
        message.text = "/admin toggle none"
        await admin.handle_admin_command(message)
        assert admin.toggle_override is None
    
    @pytest.mark.asyncio
    async def test_admin_help(self, admin):
        """Admin help should list all commands."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin help",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        text = admin.channel.reply_messages[0]['text']
        assert "reset_level" in text
        assert "clear_queue" in text
        assert "ban" in text
        assert "toggle" in text


class TestAdminBanUnban:
    """Tests for ban/unban commands."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    @pytest.mark.asyncio
    async def test_ban_user(self, admin, storage):
        """Admin should be able to ban a user."""
        # Create a user first
        await storage.get_or_create_user(12345, "testuser")
        
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ban 12345 cheating",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        user = await storage.get_user_by_telegram_id(12345)
        assert user.is_banned is True
        assert user.ban_reason == "cheating"
    
    @pytest.mark.asyncio
    async def test_unban_user(self, admin, storage):
        """Admin should be able to unban a user."""
        await storage.get_or_create_user(12345, "testuser")
        await storage.update_user_ban_status(12345, True, "test")
        
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin unban 12345",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        user = await storage.get_user_by_telegram_id(12345)
        assert user.is_banned is False
    
    @pytest.mark.asyncio
    async def test_ban_nonexistent_user(self, admin):
        """Banning a nonexistent user should fail gracefully."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ban 99998",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "不存在" in admin.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_ban_invalid_id(self, admin):
        """Banning with invalid ID should fail gracefully."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ban abc",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "无效" in admin.channel.reply_messages[0]['text']


class TestAdminResetLevel:
    """Tests for reset_level command."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    @pytest.mark.asyncio
    async def test_reset_level_success(self, admin, storage):
        """Admin should be able to reset a user's level session."""
        from ..domain.models import Session, SessionState
        
        user = await storage.get_or_create_user(12345, "testuser")
        session = Session(
            id=0, user_id=user.id, level_id=1,
            state=SessionState.FAILED_OUT, turn_count=5
        )
        await storage.upsert_session(session)
        
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin reset_level 12345 1",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        
        assert "重置" in admin.channel.reply_messages[0]['text']
        
        # Verify session was reset
        reset_session = await storage.get_session(user.id, 1)
        assert reset_session.state == SessionState.READY
        assert reset_session.turn_count == 0
    
    @pytest.mark.asyncio
    async def test_reset_level_no_session(self, admin, storage):
        """Reset for nonexistent session should fail gracefully."""
        await storage.get_or_create_user(12345, "testuser")
        
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin reset_level 12345 99",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "没有" in admin.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_reset_level_missing_args(self, admin):
        """reset_level with missing args should show usage."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin reset_level",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "用法" in admin.channel.reply_messages[0]['text']


class TestAdminClearQueue:
    """Tests for clear_queue command."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    @pytest.mark.asyncio
    async def test_clear_queue(self, admin):
        """Admin should be able to clear the queue."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin clear_queue",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "清空" in admin.channel.reply_messages[0]['text']


class TestAdminReload:
    """Tests for reload_config command."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    @pytest.mark.asyncio
    async def test_reload_config(self, admin):
        """Admin should be able to reload config."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin reload_config",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "配置" in admin.channel.reply_messages[0]['text']


class TestAdminExportLogs:
    """Tests for export_logs command."""
    
    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )
    
    @pytest.mark.asyncio
    async def test_export_logs_no_data(self, admin):
        """Export logs with no data."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin export_logs",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "没有" in admin.channel.reply_messages[0]['text'] or "日志" in admin.channel.reply_messages[0]['text']
    
    @pytest.mark.asyncio
    async def test_export_logs_invalid_date(self, admin):
        """Export logs with invalid date format."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin export_logs not-a-date",
            username="admin",
            timestamp=datetime.now()
        )
        
        await admin.handle_admin_command(message)
        assert "日期" in admin.channel.reply_messages[0]['text']


class TestAdminPingWithWorkerPool:
    """Tests for ping command with worker pool integration."""

    @pytest.fixture
    async def admin_with_workers(self, storage, content_provider):
        """Create admin commands with a real worker pool."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        processor = AsyncMock()
        worker_pool = WorkerPool(
            queue=queue,
            processor=processor,
            concurrency=4
        )
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
            worker_pool=worker_pool
        )

    @pytest.mark.asyncio
    async def test_ping_shows_total_workers(self, admin_with_workers):
        """Ping should show correct total worker count from worker pool."""
        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ping",
            username="admin",
            timestamp=datetime.now()
        )

        await admin_with_workers.handle_admin_command(message)

        text = admin_with_workers.channel.reply_messages[0]['text']
        assert "0/4" in text, f"Expected '0/4' in ping output, got: {text}"

    @pytest.mark.asyncio
    async def test_ping_shows_zero_when_no_worker_pool(self, storage, content_provider):
        """Ping should show 0/0 when worker pool is None."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        admin = AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
            worker_pool=None
        )

        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ping",
            username="admin",
            timestamp=datetime.now()
        )

        await admin.handle_admin_command(message)

        text = admin.channel.reply_messages[0]['text']
        assert "0/0" in text, f"Expected '0/0' in ping output, got: {text}"

    @pytest.mark.asyncio
    async def test_ping_shows_config_concurrency(self, storage, content_provider):
        """Ping total_workers should match the concurrency value from config."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)

        activity = content_provider.load_activity()
        expected_concurrency = activity.global_limits.worker_concurrency

        processor = AsyncMock()
        worker_pool = WorkerPool(
            queue=queue,
            processor=processor,
            concurrency=expected_concurrency
        )
        admin = AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
            worker_pool=worker_pool
        )

        message = Message(
            user_id=ADMIN_USER_ID,
            chat_id=ADMIN_USER_ID,
            message_id=1,
            text="/admin ping",
            username="admin",
            timestamp=datetime.now()
        )

        await admin.handle_admin_command(message)

        text = admin.channel.reply_messages[0]['text']
        expected = f"0/{expected_concurrency}"
        assert expected in text, f"Expected '{expected}' in ping output, got: {text}"


class TestWorkerPoolConcurrency:
    """Tests for worker pool concurrency matching config."""

    @pytest.mark.asyncio
    async def test_worker_pool_concurrency_matches_config(self, content_provider):
        """WorkerPool concurrency should match config's worker_concurrency."""
        activity = content_provider.load_activity()
        queue = MemoryQueue(max_size=100)
        processor = AsyncMock()

        pool = WorkerPool(
            queue=queue,
            processor=processor,
            concurrency=activity.global_limits.worker_concurrency
        )

        assert pool.concurrency == activity.global_limits.worker_concurrency

    @pytest.mark.asyncio
    async def test_worker_pool_starts_correct_number_of_workers(self, content_provider):
        """WorkerPool should start exactly concurrency number of worker tasks."""
        activity = content_provider.load_activity()
        queue = MemoryQueue(max_size=100)
        processor = AsyncMock()

        pool = WorkerPool(
            queue=queue,
            processor=processor,
            concurrency=activity.global_limits.worker_concurrency
        )

        await pool.start()
        try:
            assert len(pool._workers) == activity.global_limits.worker_concurrency
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_worker_pool_active_count_tracks_processing(self):
        """Active worker count should increase during task processing."""
        import asyncio

        queue = MemoryQueue(max_size=100)
        processing_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_processor(task: TaskPayload) -> None:
            processing_started.set()
            await release.wait()

        pool = WorkerPool(queue=queue, processor=slow_processor, concurrency=2)
        await pool.start()

        try:
            task = TaskPayload(
                trace_id="test-trace-1",
                user_id=1,
                telegram_user_id=1001,
                chat_id=1001,
                message_id=1,
                username="testuser",
                level_id=1,
                session_id=1,
                turn_index=1,
                user_prompt="hello"
            )
            await queue.enqueue(task)

            await asyncio.wait_for(processing_started.wait(), timeout=2.0)
            assert pool.active_workers >= 1

            release.set()
            await asyncio.sleep(0.1)
            assert pool.active_workers == 0
        finally:
            await pool.stop()


class TestAdminToggleWithTime:
    """Tests for toggle command interaction with time-based activity status."""

    @pytest.fixture
    async def admin_active(self, storage, content_provider):
        """Admin with activity within time window."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )

    @pytest.fixture
    async def admin_future(self, storage, future_content_provider):
        """Admin with future activity (not yet in time window)."""
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=future_content_provider,
            channel=channel,
            queue=queue,
        )

    def test_default_override_is_none(self, admin_active):
        """Default toggle override should be None (no override)."""
        assert admin_active.toggle_override is None

    def test_effective_enabled_when_time_active_and_no_override(self, admin_active):
        """With no override + time active, activity should be enabled."""
        assert admin_active.is_activity_enabled() is True

    def test_effective_disabled_when_time_future_and_no_override(self, admin_future):
        """With no override + future time, activity should be disabled."""
        assert admin_future.is_activity_enabled() is False

    @pytest.mark.asyncio
    async def test_force_on_overrides_future_time(self, admin_future):
        """override=on should enable activity even if time hasn't started."""
        assert admin_future.is_activity_enabled() is False

        message = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle on", username="admin", timestamp=datetime.now()
        )
        await admin_future.handle_admin_command(message)

        assert admin_future.toggle_override is True
        assert admin_future.is_activity_enabled() is True

    @pytest.mark.asyncio
    async def test_force_off_overrides_active_time(self, admin_active):
        """override=off should disable activity even if time is active."""
        assert admin_active.is_activity_enabled() is True

        message = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle off", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(message)

        assert admin_active.toggle_override is False
        assert admin_active.is_activity_enabled() is False

    @pytest.mark.asyncio
    async def test_none_restores_time_based_behavior(self, admin_active):
        """override=none should restore time-based checking."""
        # Force off first
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle off", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(msg)
        assert admin_active.is_activity_enabled() is False

        # Restore to none
        msg.text = "/admin toggle none"
        await admin_active.handle_admin_command(msg)
        assert admin_active.toggle_override is None
        # Should be enabled again because time is active
        assert admin_active.is_activity_enabled() is True

    @pytest.mark.asyncio
    async def test_none_on_future_stays_disabled(self, admin_future):
        """override=none on future activity should remain disabled by time."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle on", username="admin", timestamp=datetime.now()
        )
        await admin_future.handle_admin_command(msg)
        assert admin_future.is_activity_enabled() is True

        msg.text = "/admin toggle none"
        await admin_future.handle_admin_command(msg)
        assert admin_future.toggle_override is None
        # Should be disabled because time hasn't started
        assert admin_future.is_activity_enabled() is False

    @pytest.mark.asyncio
    async def test_toggle_no_args_shows_status(self, admin_active):
        """Toggle with no args should show current state and usage."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(msg)

        text = admin_active.channel.reply_messages[0]['text']
        assert "none" in text
        assert "用法" in text

    def test_get_activity_status_force_on(self, admin_active):
        """get_activity_status with force on should return (True, reason)."""
        admin_active._toggle_override = True
        is_enabled, reason = admin_active.get_activity_status()
        assert is_enabled is True
        assert "强制开启" in reason

    def test_get_activity_status_force_off(self, admin_active):
        """get_activity_status with force off should return (False, reason)."""
        admin_active._toggle_override = False
        is_enabled, reason = admin_active.get_activity_status()
        assert is_enabled is False
        assert "强制关闭" in reason

    def test_get_activity_status_none_delegates_to_time(self, admin_active):
        """get_activity_status with no override should use time policy."""
        admin_active._toggle_override = None
        is_enabled, reason = admin_active.get_activity_status()
        # content_provider has active time window
        assert is_enabled is True

    def test_get_activity_status_none_future(self, admin_future):
        """get_activity_status with no override + future time should be disabled."""
        admin_future._toggle_override = None
        is_enabled, reason = admin_future.get_activity_status()
        assert is_enabled is False
        assert "尚未开始" in reason


class TestAdminPingShowsTimeStatus:
    """Tests for ping command showing override, time, and effective status."""

    @pytest.fixture
    async def admin_active(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )

    @pytest.fixture
    async def admin_future(self, storage, future_content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=future_content_provider,
            channel=channel,
            queue=queue,
        )

    @pytest.mark.asyncio
    async def test_ping_shows_all_three_statuses(self, admin_active):
        """Ping should show override, time, and effective status."""
        message = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )

        await admin_active.handle_admin_command(message)

        text = admin_active.channel.reply_messages[0]['text']
        assert "活动覆写" in text
        assert "活动时间" in text
        assert "生效状态" in text

    @pytest.mark.asyncio
    async def test_ping_default_shows_none_override(self, admin_active):
        """Ping should show 'none' override by default."""
        message = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(message)

        text = admin_active.channel.reply_messages[0]['text']
        assert "none" in text
        assert "进行中" in text  # time is active
        assert "生效状态：✅ 开启" in text

    @pytest.mark.asyncio
    async def test_ping_shows_not_started_time(self, admin_future):
        """Ping should show '未开始' for future activity."""
        message = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin_future.handle_admin_command(message)

        text = admin_future.channel.reply_messages[0]['text']
        assert "未开始" in text
        assert "生效状态：❌ 关闭" in text

    @pytest.mark.asyncio
    async def test_ping_shows_force_off_override(self, admin_active):
        """Ping should reflect force-off override."""
        toggle_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle off", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(toggle_msg)

        ping_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=2,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin_active.handle_admin_command(ping_msg)

        text = admin_active.channel.reply_messages[-1]['text']
        assert "强制关闭" in text
        assert "生效状态：❌ 关闭" in text

    @pytest.mark.asyncio
    async def test_ping_shows_force_on_for_future(self, admin_future):
        """Ping should show force-on override even when time hasn't started."""
        toggle_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin toggle on", username="admin", timestamp=datetime.now()
        )
        await admin_future.handle_admin_command(toggle_msg)

        ping_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=2,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin_future.handle_admin_command(ping_msg)

        text = admin_future.channel.reply_messages[-1]['text']
        assert "强制开启" in text
        assert "未开始" in text      # time still shows not started
        assert "生效状态：✅ 开启" in text  # but effective is ON


class TestAdminToggleReward:
    """Tests for togglereward command (reward override)."""

    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )

    def test_default_reward_override_is_none(self, admin):
        """Default reward override should be None."""
        assert admin.reward_toggle_override is None

    def test_is_reward_enabled_default_with_active_time(self, admin):
        """With no override + active reward time, should be enabled."""
        assert admin.is_reward_enabled() is True

    @pytest.mark.asyncio
    async def test_togglereward_on(self, admin):
        """togglereward on should force enable rewards."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin togglereward on", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        assert admin.reward_toggle_override is True
        assert admin.is_reward_enabled() is True
        text = admin.channel.reply_messages[0]['text']
        assert "强制开启" in text

    @pytest.mark.asyncio
    async def test_togglereward_off(self, admin):
        """togglereward off should force disable rewards."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin togglereward off", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        assert admin.reward_toggle_override is False
        assert admin.is_reward_enabled() is False
        text = admin.channel.reply_messages[0]['text']
        assert "强制关闭" in text

    @pytest.mark.asyncio
    async def test_togglereward_none(self, admin):
        """togglereward none should restore time-based reward checking."""
        # Force off first
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin togglereward off", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)
        assert admin.is_reward_enabled() is False

        # Restore
        msg.text = "/admin togglereward none"
        await admin.handle_admin_command(msg)
        assert admin.reward_toggle_override is None
        # Active time window → enabled
        assert admin.is_reward_enabled() is True

    @pytest.mark.asyncio
    async def test_togglereward_no_args_shows_status(self, admin):
        """togglereward with no args should show current state."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin togglereward", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        text = admin.channel.reply_messages[0]['text']
        assert "none" in text
        assert "用法" in text

    def test_get_reward_status_force_on(self, admin):
        """get_reward_status with force on should return True."""
        admin._reward_toggle_override = True
        is_enabled, reason = admin.get_reward_status()
        assert is_enabled is True
        assert "强制开启" in reason

    def test_get_reward_status_force_off(self, admin):
        """get_reward_status with force off should return False."""
        admin._reward_toggle_override = False
        is_enabled, reason = admin.get_reward_status()
        assert is_enabled is False
        assert "强制关闭" in reason

    def test_get_reward_status_none_delegates_to_time(self, admin):
        """get_reward_status with no override should use time policy."""
        admin._reward_toggle_override = None
        is_enabled, reason = admin.get_reward_status()
        # content_provider has active time window, and no reward times → same as activity window
        assert is_enabled is True


class TestAdminPingShowsRewardStatus:
    """Tests for ping showing reward override/time/effective status."""

    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )

    @pytest.mark.asyncio
    async def test_ping_shows_reward_statuses(self, admin):
        """Ping should show reward override, time, and effective status."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        text = admin.channel.reply_messages[0]['text']
        assert "发奖覆写" in text
        assert "发奖时间" in text
        assert "发奖生效" in text

    @pytest.mark.asyncio
    async def test_ping_default_reward_none(self, admin):
        """Ping should show 'none' reward override by default."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        text = admin.channel.reply_messages[0]['text']
        assert "发奖覆写：🔄 none" in text
        assert "发奖生效：✅ 开启" in text

    @pytest.mark.asyncio
    async def test_ping_reward_force_off(self, admin):
        """Ping should show forced off reward status."""
        toggle_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin togglereward off", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(toggle_msg)

        ping_msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=2,
            text="/admin ping", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(ping_msg)

        text = admin.channel.reply_messages[-1]['text']
        assert "发奖覆写：❌ off（强制关闭）" in text
        assert "发奖生效：❌ 关闭" in text


class TestAdminHelpShowsToggleReward:
    """Test that help text includes togglereward command."""

    @pytest.fixture
    async def admin(self, storage, content_provider):
        channel = MockChannel()
        queue = MemoryQueue(max_size=100)
        return AdminCommands(
            admin_user_ids=[ADMIN_USER_ID],
            storage=storage,
            content=content_provider,
            channel=channel,
            queue=queue,
        )

    @pytest.mark.asyncio
    async def test_help_mentions_togglereward(self, admin):
        """Help should mention the togglereward command."""
        msg = Message(
            user_id=ADMIN_USER_ID, chat_id=ADMIN_USER_ID, message_id=1,
            text="/admin help", username="admin", timestamp=datetime.now()
        )
        await admin.handle_admin_command(msg)

        text = admin.channel.reply_messages[0]['text']
        assert "togglereward" in text

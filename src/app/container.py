"""Dependency injection container."""

from typing import Optional

from ..app.settings import (
    TELEGRAM_BOT_TOKEN,
    OPENAI_API_KEY,
    OPENAI_API_BASE,
    OPENAI_MODEL,
    ADMIN_USER_IDS,
    DATABASE_BACKEND,
    DATABASE_PATH,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_DATABASE,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_CHARSET,
    MYSQL_CONNECT_TIMEOUT,
    MYSQL_POOL_MIN_SIZE,
    MYSQL_POOL_MAX_SIZE,
    ACTIVITY_CONFIG_PATH,
    LEVELS_CONFIG_PATH,
    REWARDS_CONFIG_PATH,
)
from ..ports.storage import IStorage
from ..ports.content import IContentProvider
from ..ports.llm import ILLMClient
from ..ports.channel import IChannelAdapter
from ..ports.queue import IQueue

from ..adapters.storage_sqlite.sqlite_storage import SQLiteStorage
from ..adapters.storage_mysql.mysql_storage import MySQLStorage
from ..adapters.content_json.json_provider import JsonContentProvider
from ..adapters.llm_openai_compat.client import OpenAICompatibleClient, MockLLMClient
from ..adapters.telegram.adapter import TelegramAdapter
from ..adapters.queue_memory.persistent_queue import PersistentQueue

from ..domain.engine import GameEngine
from ..workers.worker import WorkerPool
from ..admin.admin_commands import AdminCommands

import logging

logger = logging.getLogger(__name__)


class Container:
    """Simple dependency injection container."""

    def __init__(self, use_mock_llm: bool = False):
        self.use_mock_llm = use_mock_llm

        self._storage: Optional[IStorage] = None
        self._content: Optional[IContentProvider] = None
        self._llm_client: Optional[ILLMClient] = None
        self._channel: Optional[IChannelAdapter] = None
        self._queue: Optional[IQueue] = None
        self._engine: Optional[GameEngine] = None
        self._worker_pool: Optional[WorkerPool] = None
        self._admin_commands: Optional[AdminCommands] = None

    def _create_storage(self) -> IStorage:
        backend = DATABASE_BACKEND.strip().lower()

        if backend == "sqlite":
            return SQLiteStorage(DATABASE_PATH)

        if backend == "mysql":
            return MySQLStorage(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                charset=MYSQL_CHARSET,
                connect_timeout=MYSQL_CONNECT_TIMEOUT,
                pool_min_size=MYSQL_POOL_MIN_SIZE,
                pool_max_size=MYSQL_POOL_MAX_SIZE,
            )

        raise ValueError(
            f"Unsupported DATABASE_BACKEND='{DATABASE_BACKEND}'. "
            "Use 'sqlite' or 'mysql'."
        )

    @property
    def storage(self) -> IStorage:
        """Get or create storage instance."""
        if self._storage is None:
            self._storage = self._create_storage()
        return self._storage

    @property
    def content(self) -> IContentProvider:
        """Get or create content provider instance."""
        if self._content is None:
            self._content = JsonContentProvider(
                activity_path=ACTIVITY_CONFIG_PATH,
                levels_path=LEVELS_CONFIG_PATH,
                rewards_path=REWARDS_CONFIG_PATH,
            )
        return self._content

    @property
    def llm_client(self) -> ILLMClient:
        """Get or create LLM client instance."""
        if self._llm_client is None:
            if self.use_mock_llm:
                self._llm_client = MockLLMClient()
            else:
                self._llm_client = OpenAICompatibleClient(
                    api_key=OPENAI_API_KEY,
                    base_url=OPENAI_API_BASE if OPENAI_API_BASE else None,
                    model=OPENAI_MODEL,
                )
        return self._llm_client

    @property
    def channel(self) -> IChannelAdapter:
        """Get or create channel adapter instance."""
        if self._channel is None:
            self._channel = TelegramAdapter(TELEGRAM_BOT_TOKEN)
        return self._channel

    @property
    def queue(self) -> IQueue:
        """Get or create queue instance."""
        if self._queue is None:
            activity = self.content.load_activity()
            self._queue = PersistentQueue(
                storage=self.storage,
                max_size=activity.global_limits.queue_max_length,
            )
        return self._queue

    @property
    def engine(self) -> GameEngine:
        """Get or create game engine instance."""
        if self._engine is None:
            self._engine = GameEngine(
                storage=self.storage,
                content=self.content,
                llm_client=self.llm_client,
                channel=self.channel,
                queue=self.queue,
            )
        return self._engine

    @property
    def worker_pool(self) -> WorkerPool:
        """Get or create worker pool instance."""
        if self._worker_pool is None:
            activity = self.content.load_activity()
            self._worker_pool = WorkerPool(
                queue=self.queue,
                processor=self.engine.process_task,
                concurrency=activity.global_limits.worker_concurrency,
            )
        return self._worker_pool

    @property
    def admin_commands(self) -> AdminCommands:
        """Get or create admin commands instance."""
        if self._admin_commands is None:
            self._admin_commands = AdminCommands(
                admin_user_ids=ADMIN_USER_IDS,
                storage=self.storage,
                content=self.content,
                channel=self.channel,
                queue=self.queue,
                worker_pool=self.worker_pool,
            )
        return self._admin_commands

    async def initialize(self) -> None:
        """Initialize all components."""
        await self.storage.initialize()

        errors = self.content.validate()
        if errors:
            raise ValueError(f"Configuration errors: {errors}")

        rewards = self.content.load_rewards()
        await self.storage.sync_reward_items(
            [
                {
                    "pool_id": r.pool_id,
                    "items": [
                        {
                            "item_id": i.item_id,
                            "type": i.type,
                            "code": i.code,
                            "max_claims_per_item": i.max_claims_per_item,
                        }
                        for i in r.items
                    ],
                }
                for r in rewards
            ]
        )

        self.engine.set_activity_enabled_fn(self.admin_commands.get_activity_status)
        self.engine.set_reward_enabled_fn(self.admin_commands.get_reward_status)

        pending_tasks = await self.storage.get_pending_tasks()
        if pending_tasks:
            restored = await self.queue.restore_from_storage(pending_tasks)
            logger.info(f"Restored {restored} pending tasks from previous session")

    async def shutdown(self) -> None:
        """Shutdown all components."""
        if self._worker_pool:
            await self._worker_pool.stop()

        if self._channel:
            await self._channel.stop()

        if self._storage:
            await self._storage.close()

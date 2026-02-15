"""MySQL storage implementation."""

import asyncio
import json
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import aiomysql
from pymysql.err import IntegrityError, OperationalError, ProgrammingError

from ...domain.models import (
    Attempt,
    ClaimResult,
    EventType,
    LevelProgress,
    RewardClaim,
    RewardClaimResponse,
    Session,
    SessionState,
    User,
)
from ...ports.storage import IStorage
from .migrations import MYSQL_MIGRATIONS, get_mysql_current_version

T = TypeVar("T")


class MySQLStorage(IStorage):
    """MySQL implementation of IStorage interface."""

    _DEADLOCK_ERROR_CODES = {1205, 1213}

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        charset: str = "utf8mb4",
        connect_timeout: float = 5.0,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        deadlock_retries: int = 3,
        deadlock_retry_delay: float = 0.05,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.charset = charset
        self.connect_timeout = connect_timeout
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.deadlock_retries = deadlock_retries
        self.deadlock_retry_delay = deadlock_retry_delay

        self._pool: Optional[aiomysql.Pool] = None

    async def initialize(self) -> None:
        """Initialize database with migrations."""
        self._pool = await aiomysql.create_pool(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            db=self.database,
            minsize=self.pool_min_size,
            maxsize=self.pool_max_size,
            autocommit=False,
            charset=self.charset,
            connect_timeout=self.connect_timeout,
            init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        )
        await self._run_migrations()

    async def close(self) -> None:
        """Close database connection pool."""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    @staticmethod
    def _error_code(exc: BaseException) -> Optional[int]:
        args = getattr(exc, "args", ())
        if args and isinstance(args[0], int):
            return args[0]
        return None

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _to_metadata(value: Any) -> Optional[dict]:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)

    async def _with_retry(self, operation: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(self.deadlock_retries + 1):
            try:
                return await operation()
            except OperationalError as exc:
                if (
                    self._error_code(exc) in self._DEADLOCK_ERROR_CODES
                    and attempt < self.deadlock_retries
                ):
                    await asyncio.sleep(self.deadlock_retry_delay * (attempt + 1))
                    continue
                raise
        raise RuntimeError("Deadlock retry loop exited unexpectedly")

    async def _run_migrations(self) -> None:
        """Run pending migrations."""
        current_version = 0

        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute("SELECT version FROM schema_version WHERE id = 1")
                    row = await cursor.fetchone()
                    current_version = row["version"] if row else 0
                except (ProgrammingError, OperationalError) as exc:
                    if self._error_code(exc) != 1146:
                        raise

        target_version = get_mysql_current_version()
        if current_version >= target_version:
            return

        for version in range(current_version, target_version):
            statements = MYSQL_MIGRATIONS[version]

            async def apply_migration() -> None:
                async with self._pool.acquire() as conn:
                    async with conn.cursor(aiomysql.DictCursor) as cursor:
                        await conn.begin()
                        try:
                            for statement in statements:
                                await cursor.execute(statement)
                            await conn.commit()
                        except Exception:
                            await conn.rollback()
                            raise

            await self._with_retry(apply_migration)

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def _execute(self, sql: str, params: tuple = ()) -> tuple[int, int]:
        async def operation() -> tuple[int, int]:
            async with self._pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(sql, params)
                    await conn.commit()
                    return cursor.rowcount, cursor.lastrowid

        return await self._with_retry(operation)

    def _row_to_user(self, row: Dict[str, Any], fallback_username: Optional[str] = None) -> User:
        return User(
            id=row["id"],
            telegram_user_id=row["telegram_user_id"],
            username=row.get("username") or fallback_username,
            is_banned=bool(row["is_banned"]),
            ban_reason=row.get("ban_reason"),
            created_at=self._to_datetime(row["created_at"]),
            updated_at=self._to_datetime(row["updated_at"]),
        )

    def _row_to_session(self, row: Dict[str, Any]) -> Session:
        return Session(
            id=row["id"],
            user_id=row["user_id"],
            level_id=row["level_id"],
            state=SessionState(row["state"]),
            turn_count=row["turn_count"],
            last_attempt_at=self._to_datetime(row.get("last_attempt_at")),
            cooldown_until=self._to_datetime(row.get("cooldown_until")),
            created_at=self._to_datetime(row["created_at"]),
            updated_at=self._to_datetime(row["updated_at"]),
        )

    def _row_to_level_progress(self, row: Dict[str, Any]) -> LevelProgress:
        return LevelProgress(
            id=row["id"],
            user_id=row["user_id"],
            level_id=row["level_id"],
            passed=bool(row["passed"]),
            passed_at=self._to_datetime(row.get("passed_at")),
            created_at=self._to_datetime(row["created_at"]),
        )

    def _row_to_reward_claim(self, row: Dict[str, Any]) -> RewardClaim:
        return RewardClaim(
            id=row["id"],
            user_id=row["user_id"],
            level_id=row["level_id"],
            pool_id=row["pool_id"],
            item_id=row["item_id"],
            reward_code=row["reward_code"],
            claimed_at=self._to_datetime(row["claimed_at"]),
        )

    # ==================== User Operations ====================

    async def get_or_create_user(self, telegram_user_id: int, username: Optional[str]) -> User:
        """Get existing user or create a new one."""
        row = await self._fetchone(
            "SELECT * FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,),
        )

        if row:
            if row.get("username") != username:
                now = datetime.now()
                await self._execute(
                    "UPDATE users SET username = %s, updated_at = %s WHERE id = %s",
                    (username, now, row["id"]),
                )
                row["username"] = username
                row["updated_at"] = now
            return self._row_to_user(row, fallback_username=username)

        now = datetime.now()
        try:
            _, user_id = await self._execute(
                """
                INSERT INTO users (telegram_user_id, username, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                """,
                (telegram_user_id, username, now, now),
            )
        except IntegrityError:
            row = await self._fetchone(
                "SELECT * FROM users WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            if not row:
                raise
            return self._row_to_user(row, fallback_username=username)

        return User(
            id=user_id,
            telegram_user_id=telegram_user_id,
            username=username,
            is_banned=False,
            ban_reason=None,
            created_at=now,
            updated_at=now,
        )

    async def get_user_by_telegram_id(self, telegram_user_id: int) -> Optional[User]:
        """Get user by Telegram ID."""
        row = await self._fetchone(
            "SELECT * FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,),
        )
        return self._row_to_user(row) if row else None

    async def update_user_ban_status(
        self,
        telegram_user_id: int,
        is_banned: bool,
        reason: Optional[str] = None,
    ) -> bool:
        """Update user's ban status."""
        rowcount, _ = await self._execute(
            """
            UPDATE users
            SET is_banned = %s, ban_reason = %s, updated_at = %s
            WHERE telegram_user_id = %s
            """,
            (int(is_banned), reason, datetime.now(), telegram_user_id),
        )
        return rowcount > 0

    # ==================== Session Operations ====================

    async def get_session(self, user_id: int, level_id: int) -> Optional[Session]:
        """Get user's session for a specific level."""
        row = await self._fetchone(
            "SELECT * FROM sessions WHERE user_id = %s AND level_id = %s",
            (user_id, level_id),
        )
        return self._row_to_session(row) if row else None

    async def upsert_session(self, session: Session) -> Session:
        """Create or update a session."""
        now = datetime.now()

        if session.id:
            await self._execute(
                """
                UPDATE sessions
                SET state = %s, turn_count = %s, last_attempt_at = %s,
                    cooldown_until = %s, updated_at = %s
                WHERE id = %s
                """,
                (
                    session.state.value,
                    session.turn_count,
                    session.last_attempt_at,
                    session.cooldown_until,
                    now,
                    session.id,
                ),
            )
        else:
            _, session_id = await self._execute(
                """
                INSERT INTO sessions
                    (user_id, level_id, state, turn_count, last_attempt_at, cooldown_until, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id),
                    state = %s,
                    turn_count = %s,
                    last_attempt_at = %s,
                    cooldown_until = %s,
                    updated_at = %s
                """,
                (
                    session.user_id,
                    session.level_id,
                    session.state.value,
                    session.turn_count,
                    session.last_attempt_at,
                    session.cooldown_until,
                    now,
                    now,
                    session.state.value,
                    session.turn_count,
                    session.last_attempt_at,
                    session.cooldown_until,
                    now,
                ),
            )
            session.id = session_id

        session.updated_at = now
        return session

    async def set_session_inflight(self, session_id: int) -> bool:
        """Mark session as inflight (processing). Only succeeds from READY state."""
        rowcount, _ = await self._execute(
            """
            UPDATE sessions
            SET state = %s, updated_at = %s
            WHERE id = %s AND state = %s
            """,
            (
                SessionState.INFLIGHT.value,
                datetime.now(),
                session_id,
                SessionState.READY.value,
            ),
        )
        return rowcount > 0

    async def clear_session_inflight(
        self,
        session_id: int,
        new_state: SessionState,
        cooldown_until: Optional[datetime] = None,
    ) -> bool:
        """Clear inflight status and set new state."""
        rowcount, _ = await self._execute(
            """
            UPDATE sessions
            SET state = %s, cooldown_until = %s, updated_at = %s
            WHERE id = %s
            """,
            (new_state.value, cooldown_until, datetime.now(), session_id),
        )
        return rowcount > 0

    async def reset_session(self, user_id: int, level_id: int) -> bool:
        """Reset a user's session for a specific level."""
        rowcount, _ = await self._execute(
            """
            UPDATE sessions
            SET state = %s, turn_count = 0, cooldown_until = NULL, updated_at = %s
            WHERE user_id = %s AND level_id = %s
            """,
            (SessionState.READY.value, datetime.now(), user_id, level_id),
        )
        return rowcount > 0

    # ==================== Level Progress ====================

    async def is_level_passed(self, user_id: int, level_id: int) -> bool:
        """Check if user has passed a level."""
        row = await self._fetchone(
            "SELECT passed FROM level_progress WHERE user_id = %s AND level_id = %s",
            (user_id, level_id),
        )
        return bool(row["passed"]) if row else False

    async def mark_level_passed(self, user_id: int, level_id: int) -> None:
        """Mark a level as passed for a user."""
        now = datetime.now()
        await self._execute(
            """
            INSERT INTO level_progress (user_id, level_id, passed, passed_at, created_at)
            VALUES (%s, %s, 1, %s, %s)
            ON DUPLICATE KEY UPDATE
                passed = 1,
                passed_at = %s
            """,
            (user_id, level_id, now, now, now),
        )

    async def get_current_level(self, user_id: int, total_levels: int) -> int:
        """Get user's current level (first unpassed level)."""
        rows = await self._fetchall(
            """
            SELECT level_id FROM level_progress
            WHERE user_id = %s AND passed = 1
            ORDER BY level_id
            """,
            (user_id,),
        )

        passed_levels = {row["level_id"] for row in rows}

        for level_id in range(1, total_levels + 1):
            if level_id not in passed_levels:
                return level_id

        return total_levels + 1

    async def get_user_progress(self, user_id: int) -> List[LevelProgress]:
        """Get all level progress for a user."""
        rows = await self._fetchall(
            "SELECT * FROM level_progress WHERE user_id = %s ORDER BY level_id",
            (user_id,),
        )
        return [self._row_to_level_progress(row) for row in rows]

    # ==================== Attempts ====================

    async def record_attempt(self, attempt: Attempt) -> Attempt:
        """Record an attempt at a level."""
        _, attempt_id = await self._execute(
            """
            INSERT INTO attempts
                (user_id, level_id, session_id, turn_index, user_prompt, llm_output,
                 keyword_verdict, judge_verdict, final_verdict, grade_reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                attempt.user_id,
                attempt.level_id,
                attempt.session_id,
                attempt.turn_index,
                attempt.user_prompt,
                attempt.llm_output,
                attempt.keyword_verdict.value if attempt.keyword_verdict else None,
                attempt.judge_verdict.value if attempt.judge_verdict else None,
                attempt.final_verdict.value if attempt.final_verdict else None,
                attempt.grade_reason,
                attempt.created_at,
            ),
        )
        attempt.id = attempt_id
        return attempt

    # ==================== Rewards ====================

    async def sync_reward_items(self, pools: List[dict]) -> None:
        """Sync reward items from config to database."""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await conn.begin()
                try:
                    for pool in pools:
                        pool_id = pool["pool_id"]
                        for item in pool["items"]:
                            await cursor.execute(
                                """
                                INSERT INTO reward_items (pool_id, item_id, type, code, max_claims_per_item)
                                VALUES (%s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    pool_id = %s,
                                    type = %s,
                                    code = %s,
                                    max_claims_per_item = %s,
                                    updated_at = CURRENT_TIMESTAMP(6)
                                """,
                                (
                                    pool_id,
                                    item["item_id"],
                                    item["type"],
                                    item["code"],
                                    item["max_claims_per_item"],
                                    pool_id,
                                    item["type"],
                                    item["code"],
                                    item["max_claims_per_item"],
                                ),
                            )
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

    async def claim_reward(
        self,
        pool_id: str,
        user_id: int,
        level_id: int,
    ) -> RewardClaimResponse:
        """Atomically claim a reward from a pool."""

        async def operation() -> RewardClaimResponse:
            now = datetime.now()
            async with self._pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await conn.begin()
                    try:
                        await cursor.execute(
                            "SELECT id FROM reward_claims WHERE user_id = %s AND level_id = %s LIMIT 1",
                            (user_id, level_id),
                        )
                        existing = await cursor.fetchone()
                        if existing:
                            await conn.rollback()
                            return RewardClaimResponse(
                                result=ClaimResult.ALREADY_CLAIMED,
                                message="You have already claimed a reward for this level",
                            )

                        await cursor.execute(
                            """
                            SELECT item_id, code FROM reward_items
                            WHERE pool_id = %s AND claimed_count < max_claims_per_item
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE
                            """,
                            (pool_id,),
                        )
                        item = await cursor.fetchone()
                        if not item:
                            await conn.rollback()
                            return RewardClaimResponse(
                                result=ClaimResult.NO_STOCK,
                                message="No rewards available in this pool",
                            )

                        await cursor.execute(
                            """
                            UPDATE reward_items
                            SET claimed_count = claimed_count + 1, updated_at = %s
                            WHERE item_id = %s AND claimed_count < max_claims_per_item
                            """,
                            (now, item["item_id"]),
                        )
                        if cursor.rowcount == 0:
                            await conn.rollback()
                            return RewardClaimResponse(
                                result=ClaimResult.NO_STOCK,
                                message="Reward was claimed by another user",
                            )

                        await cursor.execute(
                            """
                            INSERT INTO reward_claims
                                (user_id, level_id, pool_id, item_id, reward_code, claimed_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (user_id, level_id, pool_id, item["item_id"], item["code"], now),
                        )

                        await conn.commit()
                        return RewardClaimResponse(
                            result=ClaimResult.SUCCESS,
                            reward_code=item["code"],
                            item_id=item["item_id"],
                            message="Reward claimed successfully",
                        )
                    except IntegrityError as exc:
                        await conn.rollback()
                        if self._error_code(exc) == 1062:
                            return RewardClaimResponse(
                                result=ClaimResult.ALREADY_CLAIMED,
                                message="You have already claimed a reward for this level",
                            )
                        raise
                    except Exception:
                        await conn.rollback()
                        raise

        return await self._with_retry(operation)

    async def get_user_claims(self, user_id: int) -> List[RewardClaim]:
        """Get all reward claims for a user."""
        rows = await self._fetchall(
            "SELECT * FROM reward_claims WHERE user_id = %s ORDER BY claimed_at",
            (user_id,),
        )
        return [self._row_to_reward_claim(row) for row in rows]

    # ==================== Queue Persistence ====================

    async def save_pending_task(self, task: "TaskPayload") -> int:
        """Save a pending task to persistent storage."""
        _, task_id = await self._execute(
            """
            INSERT INTO pending_tasks
                (trace_id, user_id, telegram_user_id, chat_id, message_id, username,
                 level_id, session_id, user_prompt, turn_index, enqueued_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE trace_id = trace_id
            """,
            (
                task.trace_id,
                task.user_id,
                task.telegram_user_id,
                task.chat_id,
                task.message_id,
                task.username,
                task.level_id,
                task.session_id,
                task.user_prompt,
                task.turn_index,
                task.enqueued_at,
                datetime.now(),
            ),
        )
        return task_id or 0

    async def delete_pending_task(self, trace_id: str) -> bool:
        """Delete a pending task by trace_id."""
        rowcount, _ = await self._execute(
            "DELETE FROM pending_tasks WHERE trace_id = %s",
            (trace_id,),
        )
        return rowcount > 0

    async def get_pending_tasks(self) -> List["TaskPayload"]:
        """Get all pending tasks ordered by enqueued_at time."""
        from ...domain.models import TaskPayload

        rows = await self._fetchall(
            "SELECT * FROM pending_tasks ORDER BY enqueued_at ASC"
        )

        return [
            TaskPayload(
                trace_id=row["trace_id"],
                user_id=row["user_id"],
                telegram_user_id=row["telegram_user_id"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                username=row.get("username"),
                level_id=row["level_id"],
                session_id=row["session_id"],
                user_prompt=row["user_prompt"],
                turn_index=row["turn_index"],
                enqueued_at=self._to_datetime(row["enqueued_at"]),
            )
            for row in rows
        ]

    # ==================== Logging ====================

    async def append_log_event(
        self,
        trace_id: str,
        event_type: EventType,
        telegram_user_id: int,
        chat_id: int,
        content: str,
        level_id: Optional[int] = None,
        session_id: Optional[int] = None,
        turn_index: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Append a log event."""
        max_content_length = 500
        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."

        await self._execute(
            """
            INSERT INTO log_events
                (trace_id, event_type, telegram_user_id, chat_id, level_id,
                 session_id, turn_index, content, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trace_id,
                event_type.value,
                telegram_user_id,
                chat_id,
                level_id,
                session_id,
                turn_index,
                content,
                json.dumps(metadata) if metadata else None,
                datetime.now(),
            ),
        )

    async def export_logs(
        self,
        date: Optional[datetime] = None,
        mask_codes: bool = True,
    ) -> List[dict]:
        """Export logs for review."""
        if date:
            rows = await self._fetchall(
                "SELECT * FROM log_events WHERE DATE(created_at) = %s ORDER BY created_at",
                (date.strftime("%Y-%m-%d"),),
            )
        else:
            rows = await self._fetchall("SELECT * FROM log_events ORDER BY created_at")

        logs = []
        for row in rows:
            log_entry = {
                "id": row["id"],
                "trace_id": row["trace_id"],
                "event_type": row["event_type"],
                "telegram_user_id": row["telegram_user_id"],
                "chat_id": row["chat_id"],
                "level_id": row.get("level_id"),
                "session_id": row.get("session_id"),
                "turn_index": row.get("turn_index"),
                "content": row.get("content"),
                "metadata": self._to_metadata(row.get("metadata")),
                "created_at": self._to_datetime(row["created_at"]).isoformat(),
            }

            if mask_codes and row["event_type"] == "REWARD_CLAIM":
                if log_entry.get("metadata") and "reward_code" in log_entry["metadata"]:
                    code = log_entry["metadata"]["reward_code"]
                    log_entry["metadata"]["reward_code"] = code[:4] + "****" if len(code) > 4 else "****"

            logs.append(log_entry)

        return logs

    # ==================== Stats ====================

    async def get_stats(self) -> dict:
        """Get activity statistics."""
        today = date.today().isoformat()

        today_attempts = (await self._fetchone(
            "SELECT COUNT(*) AS count FROM attempts WHERE DATE(created_at) = %s",
            (today,),
        ))["count"]

        today_claims = (await self._fetchone(
            "SELECT COUNT(*) AS count FROM reward_claims WHERE DATE(claimed_at) = %s",
            (today,),
        ))["count"]

        total_users = (await self._fetchone("SELECT COUNT(*) AS count FROM users"))["count"]

        passed_rows = await self._fetchall(
            """
            SELECT level_id, COUNT(*) AS count
            FROM level_progress
            WHERE passed = 1
            GROUP BY level_id
            ORDER BY level_id
            """
        )
        passed_by_level = {row["level_id"]: row["count"] for row in passed_rows}

        reward_rows = await self._fetchall(
            """
            SELECT pool_id,
                   SUM(max_claims_per_item) AS total,
                   SUM(claimed_count) AS claimed
            FROM reward_items
            GROUP BY pool_id
            """
        )
        reward_stock = {
            row["pool_id"]: {
                "total": row["total"],
                "claimed": row["claimed"],
                "remaining": row["total"] - row["claimed"],
            }
            for row in reward_rows
        }

        return {
            "today_attempts": today_attempts,
            "today_claims": today_claims,
            "total_users": total_users,
            "passed_by_level": passed_by_level,
            "reward_stock": reward_stock,
        }

"""SQLite storage implementation."""

import aiosqlite
import asyncio
import json
from pathlib import Path
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from ...ports.storage import IStorage
from ...ports.content import RewardPoolConfig
from ...domain.models import (
    User, Session, Attempt, LevelProgress, RewardClaim,
    LogEvent, RewardClaimResponse, SessionState, EventType,
    ClaimResult
)
from .migrations import get_migration_sql, get_current_version


class SQLiteStorage(IStorage):
    """SQLite implementation of IStorage interface."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """Initialize database with migrations."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._connection = await aiosqlite.connect(str(self.db_path))
        self._connection.row_factory = aiosqlite.Row
        
        # Configure SQLite for better concurrency
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA busy_timeout=5000")
        await self._connection.execute("PRAGMA synchronous=NORMAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        
        # Run migrations
        await self._run_migrations()
    
    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
    
    async def _run_migrations(self) -> None:
        """Run pending migrations."""
        # Check current version
        try:
            cursor = await self._connection.execute(
                "SELECT version FROM schema_version LIMIT 1"
            )
            row = await cursor.fetchone()
            current_version = row['version'] if row else 0
        except aiosqlite.OperationalError:
            current_version = 0
        
        target_version = get_current_version()
        
        if current_version < target_version:
            migration_sql = get_migration_sql(current_version)
            await self._connection.executescript(migration_sql)
            await self._connection.commit()
    
    # ==================== User Operations ====================
    
    async def get_or_create_user(
        self, 
        telegram_user_id: int, 
        username: Optional[str]
    ) -> User:
        """Get existing user or create a new one."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,)
            )
            row = await cursor.fetchone()
            
            if row:
                # Update username if changed
                if row['username'] != username:
                    await self._connection.execute(
                        "UPDATE users SET username = ?, updated_at = ? WHERE id = ?",
                        (username, datetime.now().isoformat(), row['id'])
                    )
                    await self._connection.commit()
                
                return User(
                    id=row['id'],
                    telegram_user_id=row['telegram_user_id'],
                    username=row['username'] or username,
                    is_banned=bool(row['is_banned']),
                    ban_reason=row['ban_reason'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at'])
                )
            
            # Create new user
            now = datetime.now().isoformat()
            cursor = await self._connection.execute(
                """INSERT INTO users (telegram_user_id, username, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (telegram_user_id, username, now, now)
            )
            await self._connection.commit()
            
            return User(
                id=cursor.lastrowid,
                telegram_user_id=telegram_user_id,
                username=username,
                is_banned=False,
                ban_reason=None,
                created_at=datetime.fromisoformat(now),
                updated_at=datetime.fromisoformat(now)
            )
    
    async def get_user_by_telegram_id(self, telegram_user_id: int) -> Optional[User]:
        """Get user by Telegram ID."""
        cursor = await self._connection.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        return User(
            id=row['id'],
            telegram_user_id=row['telegram_user_id'],
            username=row['username'],
            is_banned=bool(row['is_banned']),
            ban_reason=row['ban_reason'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at'])
        )
    
    async def update_user_ban_status(
        self, 
        telegram_user_id: int, 
        is_banned: bool, 
        reason: Optional[str] = None
    ) -> bool:
        """Update user's ban status."""
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE users 
                   SET is_banned = ?, ban_reason = ?, updated_at = ?
                   WHERE telegram_user_id = ?""",
                (int(is_banned), reason, datetime.now().isoformat(), telegram_user_id)
            )
            await self._connection.commit()
            return cursor.rowcount > 0
    
    # ==================== Session Operations ====================
    
    async def get_session(self, user_id: int, level_id: int) -> Optional[Session]:
        """Get user's session for a specific level."""
        cursor = await self._connection.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND level_id = ?",
            (user_id, level_id)
        )
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        return Session(
            id=row['id'],
            user_id=row['user_id'],
            level_id=row['level_id'],
            state=SessionState(row['state']),
            turn_count=row['turn_count'],
            last_attempt_at=datetime.fromisoformat(row['last_attempt_at']) if row['last_attempt_at'] else None,
            cooldown_until=datetime.fromisoformat(row['cooldown_until']) if row['cooldown_until'] else None,
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at'])
        )
    
    async def upsert_session(self, session: Session) -> Session:
        """Create or update a session."""
        async with self._lock:
            now = datetime.now().isoformat()
            
            if session.id:
                # Update existing
                await self._connection.execute(
                    """UPDATE sessions 
                       SET state = ?, turn_count = ?, last_attempt_at = ?, 
                           cooldown_until = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        session.state.value,
                        session.turn_count,
                        session.last_attempt_at.isoformat() if session.last_attempt_at else None,
                        session.cooldown_until.isoformat() if session.cooldown_until else None,
                        now,
                        session.id
                    )
                )
            else:
                # Insert new
                cursor = await self._connection.execute(
                    """INSERT INTO sessions 
                       (user_id, level_id, state, turn_count, last_attempt_at, cooldown_until, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, level_id) DO UPDATE SET
                       state = excluded.state,
                       turn_count = excluded.turn_count,
                       last_attempt_at = excluded.last_attempt_at,
                       cooldown_until = excluded.cooldown_until,
                       updated_at = excluded.updated_at""",
                    (
                        session.user_id,
                        session.level_id,
                        session.state.value,
                        session.turn_count,
                        session.last_attempt_at.isoformat() if session.last_attempt_at else None,
                        session.cooldown_until.isoformat() if session.cooldown_until else None,
                        now,
                        now
                    )
                )
                session.id = cursor.lastrowid
            
            await self._connection.commit()
            session.updated_at = datetime.fromisoformat(now)
            return session
    
    async def set_session_inflight(self, session_id: int) -> bool:
        """Mark session as inflight (processing). Only succeeds from READY state."""
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE sessions 
                   SET state = ?, updated_at = ?
                   WHERE id = ? AND state = ?""",
                (
                    SessionState.INFLIGHT.value,
                    datetime.now().isoformat(),
                    session_id,
                    SessionState.READY.value
                )
            )
            await self._connection.commit()
            return cursor.rowcount > 0
    
    async def clear_session_inflight(
        self, 
        session_id: int, 
        new_state: SessionState,
        cooldown_until: Optional[datetime] = None
    ) -> bool:
        """Clear inflight status and set new state."""
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE sessions 
                   SET state = ?, cooldown_until = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    new_state.value,
                    cooldown_until.isoformat() if cooldown_until else None,
                    datetime.now().isoformat(),
                    session_id
                )
            )
            await self._connection.commit()
            return cursor.rowcount > 0
    
    async def reset_session(self, user_id: int, level_id: int) -> bool:
        """Reset a user's session for a specific level."""
        async with self._lock:
            cursor = await self._connection.execute(
                """UPDATE sessions 
                   SET state = ?, turn_count = 0, cooldown_until = NULL, updated_at = ?
                   WHERE user_id = ? AND level_id = ?""",
                (
                    SessionState.READY.value,
                    datetime.now().isoformat(),
                    user_id,
                    level_id
                )
            )
            await self._connection.commit()
            return cursor.rowcount > 0
    
    # ==================== Level Progress ====================
    
    async def is_level_passed(self, user_id: int, level_id: int) -> bool:
        """Check if user has passed a level."""
        cursor = await self._connection.execute(
            "SELECT passed FROM level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, level_id)
        )
        row = await cursor.fetchone()
        return bool(row['passed']) if row else False
    
    async def mark_level_passed(self, user_id: int, level_id: int) -> None:
        """Mark a level as passed for a user."""
        async with self._lock:
            now = datetime.now().isoformat()
            await self._connection.execute(
                """INSERT INTO level_progress (user_id, level_id, passed, passed_at, created_at)
                   VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(user_id, level_id) DO UPDATE SET
                   passed = 1, passed_at = excluded.passed_at""",
                (user_id, level_id, now, now)
            )
            await self._connection.commit()
    
    async def get_current_level(self, user_id: int, total_levels: int) -> int:
        """Get user's current level (first unpassed level)."""
        cursor = await self._connection.execute(
            """SELECT level_id FROM level_progress 
               WHERE user_id = ? AND passed = 1
               ORDER BY level_id""",
            (user_id,)
        )
        rows = await cursor.fetchall()
        
        passed_levels = {row['level_id'] for row in rows}
        
        for level_id in range(1, total_levels + 1):
            if level_id not in passed_levels:
                return level_id
        
        return total_levels + 1  # All levels passed
    
    async def get_user_progress(self, user_id: int) -> List[LevelProgress]:
        """Get all level progress for a user."""
        cursor = await self._connection.execute(
            "SELECT * FROM level_progress WHERE user_id = ? ORDER BY level_id",
            (user_id,)
        )
        rows = await cursor.fetchall()
        
        return [
            LevelProgress(
                id=row['id'],
                user_id=row['user_id'],
                level_id=row['level_id'],
                passed=bool(row['passed']),
                passed_at=datetime.fromisoformat(row['passed_at']) if row['passed_at'] else None,
                created_at=datetime.fromisoformat(row['created_at'])
            )
            for row in rows
        ]
    
    # ==================== Attempts ====================
    
    async def record_attempt(self, attempt: Attempt) -> Attempt:
        """Record an attempt at a level."""
        async with self._lock:
            cursor = await self._connection.execute(
                """INSERT INTO attempts 
                   (user_id, level_id, session_id, turn_index, user_prompt, llm_output,
                    keyword_verdict, judge_verdict, final_verdict, grade_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    attempt.created_at.isoformat()
                )
            )
            await self._connection.commit()
            attempt.id = cursor.lastrowid
            return attempt
    
    # ==================== Rewards ====================
    
    async def sync_reward_items(self, pools: List[dict]) -> None:
        """Sync reward items from config to database."""
        async with self._lock:
            for pool in pools:
                pool_id = pool['pool_id']
                for item in pool['items']:
                    await self._connection.execute(
                        """INSERT INTO reward_items (pool_id, item_id, type, code, max_claims_per_item)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(item_id) DO UPDATE SET
                           pool_id = excluded.pool_id,
                           type = excluded.type,
                           code = excluded.code,
                           max_claims_per_item = excluded.max_claims_per_item,
                           updated_at = CURRENT_TIMESTAMP""",
                        (
                            pool_id,
                            item['item_id'],
                            item['type'],
                            item['code'],
                            item['max_claims_per_item']
                        )
                    )
            await self._connection.commit()
    
    async def claim_reward(
        self, 
        pool_id: str, 
        user_id: int, 
        level_id: int
    ) -> RewardClaimResponse:
        """Atomically claim a reward from a pool."""
        async with self._lock:
            # Check if already claimed for this level
            cursor = await self._connection.execute(
                "SELECT * FROM reward_claims WHERE user_id = ? AND level_id = ?",
                (user_id, level_id)
            )
            existing = await cursor.fetchone()
            
            if existing:
                return RewardClaimResponse(
                    result=ClaimResult.ALREADY_CLAIMED,
                    message="You have already claimed a reward for this level"
                )
            
            # Find available item in pool
            cursor = await self._connection.execute(
                """SELECT * FROM reward_items 
                   WHERE pool_id = ? AND claimed_count < max_claims_per_item
                   ORDER BY id
                   LIMIT 1""",
                (pool_id,)
            )
            item = await cursor.fetchone()
            
            if not item:
                return RewardClaimResponse(
                    result=ClaimResult.NO_STOCK,
                    message="No rewards available in this pool"
                )
            
            # Atomically claim the item
            now = datetime.now().isoformat()
            
            # Update claimed_count with condition check
            cursor = await self._connection.execute(
                """UPDATE reward_items 
                   SET claimed_count = claimed_count + 1, updated_at = ?
                   WHERE item_id = ? AND claimed_count < max_claims_per_item""",
                (now, item['item_id'])
            )
            
            if cursor.rowcount == 0:
                # Race condition - item was claimed by another
                return RewardClaimResponse(
                    result=ClaimResult.NO_STOCK,
                    message="Reward was claimed by another user"
                )
            
            # Record the claim
            await self._connection.execute(
                """INSERT INTO reward_claims (user_id, level_id, pool_id, item_id, reward_code, claimed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, level_id, pool_id, item['item_id'], item['code'], now)
            )
            
            await self._connection.commit()
            
            return RewardClaimResponse(
                result=ClaimResult.SUCCESS,
                reward_code=item['code'],
                item_id=item['item_id'],
                message="Reward claimed successfully"
            )
    
    async def get_user_claims(self, user_id: int) -> List[RewardClaim]:
        """Get all reward claims for a user."""
        cursor = await self._connection.execute(
            "SELECT * FROM reward_claims WHERE user_id = ? ORDER BY claimed_at",
            (user_id,)
        )
        rows = await cursor.fetchall()
        
        return [
            RewardClaim(
                id=row['id'],
                user_id=row['user_id'],
                level_id=row['level_id'],
                pool_id=row['pool_id'],
                item_id=row['item_id'],
                reward_code=row['reward_code'],
                claimed_at=datetime.fromisoformat(row['claimed_at'])
            )
            for row in rows
        ]
    
    # ==================== Queue Persistence ====================
    
    async def save_pending_task(self, task: 'TaskPayload') -> int:
        """Save a pending task to persistent storage."""
        from ...domain.models import TaskPayload
        
        async with self._lock:
            cursor = await self._connection.execute(
                """INSERT INTO pending_tasks 
                   (trace_id, user_id, telegram_user_id, chat_id, message_id, username,
                    level_id, session_id, user_prompt, turn_index, enqueued_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trace_id) DO NOTHING""",
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
                    task.enqueued_at.isoformat(),
                    datetime.now().isoformat()
                )
            )
            await self._connection.commit()
            return cursor.lastrowid or 0
    
    async def delete_pending_task(self, trace_id: str) -> bool:
        """Delete a pending task by trace_id."""
        async with self._lock:
            cursor = await self._connection.execute(
                "DELETE FROM pending_tasks WHERE trace_id = ?",
                (trace_id,)
            )
            await self._connection.commit()
            return cursor.rowcount > 0
    
    async def get_pending_tasks(self) -> List['TaskPayload']:
        """Get all pending tasks ordered by enqueued_at time."""
        from ...domain.models import TaskPayload
        
        cursor = await self._connection.execute(
            """SELECT * FROM pending_tasks ORDER BY enqueued_at ASC"""
        )
        rows = await cursor.fetchall()
        
        return [
            TaskPayload(
                trace_id=row['trace_id'],
                user_id=row['user_id'],
                telegram_user_id=row['telegram_user_id'],
                chat_id=row['chat_id'],
                message_id=row['message_id'],
                username=row['username'],
                level_id=row['level_id'],
                session_id=row['session_id'],
                user_prompt=row['user_prompt'],
                turn_index=row['turn_index'],
                enqueued_at=datetime.fromisoformat(row['enqueued_at'])
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
        metadata: Optional[dict] = None
    ) -> None:
        """Append a log event."""
        # Truncate content if needed
        max_content_length = 500
        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."
        
        async with self._lock:
            await self._connection.execute(
                """INSERT INTO log_events 
                   (trace_id, event_type, telegram_user_id, chat_id, level_id, 
                    session_id, turn_index, content, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    datetime.now().isoformat()
                )
            )
            await self._connection.commit()
    
    async def export_logs(
        self, 
        date: Optional[datetime] = None,
        mask_codes: bool = True
    ) -> List[dict]:
        """Export logs for review."""
        if date:
            date_str = date.strftime("%Y-%m-%d")
            cursor = await self._connection.execute(
                """SELECT * FROM log_events 
                   WHERE date(created_at) = ?
                   ORDER BY created_at""",
                (date_str,)
            )
        else:
            cursor = await self._connection.execute(
                "SELECT * FROM log_events ORDER BY created_at"
            )
        
        rows = await cursor.fetchall()
        logs = []
        
        for row in rows:
            log_entry = {
                'id': row['id'],
                'trace_id': row['trace_id'],
                'event_type': row['event_type'],
                'telegram_user_id': row['telegram_user_id'],
                'chat_id': row['chat_id'],
                'level_id': row['level_id'],
                'session_id': row['session_id'],
                'turn_index': row['turn_index'],
                'content': row['content'],
                'metadata': json.loads(row['metadata']) if row['metadata'] else None,
                'created_at': row['created_at']
            }
            
            # Mask reward codes if requested
            if mask_codes and row['event_type'] == 'REWARD_CLAIM':
                if log_entry.get('metadata') and 'reward_code' in log_entry['metadata']:
                    code = log_entry['metadata']['reward_code']
                    log_entry['metadata']['reward_code'] = code[:4] + '****' if len(code) > 4 else '****'
            
            logs.append(log_entry)
        
        return logs
    
    # ==================== Stats ====================
    
    async def get_stats(self) -> dict:
        """Get activity statistics."""
        today = date.today().isoformat()
        
        # Today's attempts
        cursor = await self._connection.execute(
            "SELECT COUNT(*) as count FROM attempts WHERE date(created_at) = ?",
            (today,)
        )
        today_attempts = (await cursor.fetchone())['count']
        
        # Today's claims
        cursor = await self._connection.execute(
            "SELECT COUNT(*) as count FROM reward_claims WHERE date(claimed_at) = ?",
            (today,)
        )
        today_claims = (await cursor.fetchone())['count']
        
        # Total users
        cursor = await self._connection.execute("SELECT COUNT(*) as count FROM users")
        total_users = (await cursor.fetchone())['count']
        
        # Passed by level
        cursor = await self._connection.execute(
            """SELECT level_id, COUNT(*) as count 
               FROM level_progress WHERE passed = 1 
               GROUP BY level_id ORDER BY level_id"""
        )
        passed_by_level = {row['level_id']: row['count'] for row in await cursor.fetchall()}
        
        # Reward stock
        cursor = await self._connection.execute(
            """SELECT pool_id, 
                      SUM(max_claims_per_item) as total,
                      SUM(claimed_count) as claimed
               FROM reward_items GROUP BY pool_id"""
        )
        reward_stock = {
            row['pool_id']: {
                'total': row['total'],
                'claimed': row['claimed'],
                'remaining': row['total'] - row['claimed']
            }
            for row in await cursor.fetchall()
        }
        
        return {
            'today_attempts': today_attempts,
            'today_claims': today_claims,
            'total_users': total_users,
            'passed_by_level': passed_by_level,
            'reward_stock': reward_stock
        }

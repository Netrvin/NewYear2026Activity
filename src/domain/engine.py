"""Game engine - core state machine and game logic."""

import logging
import uuid
from datetime import datetime
from typing import Callable, Optional, Tuple

from .models import (
    User, Session, SessionState, Attempt, TaskPayload,
    GradeVerdict, GradeResult, ClaimResult, Message, LLMResult,
    RewardClaimResponse, EventType
)
from .grading import CompositeGrader
from .policies import (
    UnlockPolicy, CooldownPolicy, TurnPolicy,
    InputValidationPolicy, ActivityTimePolicy, BanPolicy,
    RewardTimePolicy
)
from ..ports.storage import IStorage
from ..ports.content import IContentProvider, LevelConfig
from ..ports.llm import ILLMClient
from ..ports.channel import IChannelAdapter
from ..ports.queue import IQueue

logger = logging.getLogger(__name__)


class GameEngine:
    """Core game engine handling state machine and game logic."""
    
    def __init__(
        self,
        storage: IStorage,
        content: IContentProvider,
        llm_client: ILLMClient,
        channel: IChannelAdapter,
        queue: IQueue,
        activity_enabled_fn: Callable[[], tuple[bool, str]] | None = None,
        reward_enabled_fn: Callable[[], tuple[bool, str]] | None = None
    ):
        self.storage = storage
        self.content = content
        self.llm_client = llm_client
        self.channel = channel
        self.queue = queue
        self.grader = CompositeGrader(llm_client)
        self._activity_enabled_fn = activity_enabled_fn
        self._reward_enabled_fn = reward_enabled_fn
    
    def set_activity_enabled_fn(self, fn: Callable[[], tuple[bool, str]]) -> None:
        """Set external activity enabled checker (e.g. from AdminCommands)."""
        self._activity_enabled_fn = fn
    
    def set_reward_enabled_fn(self, fn: Callable[[], tuple[bool, str]]) -> None:
        """Set external reward enabled checker (e.g. from AdminCommands)."""
        self._reward_enabled_fn = fn
    
    def _is_activity_enabled(self) -> tuple[bool, str]:
        """Check if activity is enabled, using external checker or time policy."""
        if self._activity_enabled_fn is not None:
            return self._activity_enabled_fn()
        # Fallback to time-based check
        activity = self.content.load_activity()
        return ActivityTimePolicy.is_activity_active(activity)
    
    def _is_reward_enabled(self) -> tuple[bool, str]:
        """Check if reward is enabled, using external checker or time policy."""
        if self._reward_enabled_fn is not None:
            return self._reward_enabled_fn()
        # Fallback to time-based check
        activity = self.content.load_activity()
        return RewardTimePolicy.is_reward_active(activity)
    
    async def handle_message(self, message: Message) -> None:
        """Handle an incoming user message (answer attempt)."""
        trace_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{trace_id}] 用户答题尝试: user_id={message.user_id}, nickname={message.nickname}, text={message.text!r}")
        
        # Log user input
        await self.storage.append_log_event(
            trace_id=trace_id,
            event_type=EventType.USER_IN,
            telegram_user_id=message.user_id,
            chat_id=message.chat_id,
            content=message.text
        )
        
        # Get or create user
        user = await self.storage.get_or_create_user(
            telegram_user_id=message.user_id,
            username=message.username
        )
        
        # Check if user is banned
        is_banned, ban_reason = BanPolicy.is_banned(user)
        if is_banned:
            await self._send_response(message, ban_reason, trace_id)
            return
        
        # Check if activity is active
        is_active, activity_reason = self._is_activity_enabled()
        if not is_active:
            await self._send_response(message, activity_reason, trace_id)
            return
        
        # Get current level
        levels = self.content.load_levels()
        total_levels = len(levels)
        current_level_id = await self.storage.get_current_level(user.id, total_levels)
        
        # Check if all levels passed
        if current_level_id > total_levels or await self._all_levels_passed(user.id, total_levels):
            await self._send_response(
                message,
                "🎉 恭喜！你已通关全部关卡！\n\n感谢参与 2026 马年极客闯关活动！",
                trace_id
            )
            return
        
        level_config = self.content.get_level(current_level_id)
        if not level_config or not level_config.enabled:
            await self._send_response(message, "当前关卡不可用", trace_id)
            return
        
        # Validate input
        is_valid, validation_msg = InputValidationPolicy.validate_input(
            message.text,
            level_config.limits.max_input_chars
        )
        if not is_valid:
            await self._send_response(message, f"❌ {validation_msg}", trace_id)
            return
        
        # Get or create session
        session = await self.storage.get_session(user.id, current_level_id)
        if not session:
            session = Session(
                id=0,
                user_id=user.id,
                level_id=current_level_id,
                state=SessionState.READY,
                turn_count=0
            )
            session = await self.storage.upsert_session(session)
        
        # Check session state
        state_check = await self._check_session_state(session, level_config, user.id)
        if state_check:
            await self._send_response(message, state_check, trace_id)
            return
        
        # Check turn limit
        can_attempt, turn_msg = TurnPolicy.can_attempt(session, level_config.limits.max_turns)
        if not can_attempt:
            session.state = SessionState.FAILED_OUT
            await self.storage.upsert_session(session)
            await self._send_response(
                message,
                f"❌ {turn_msg}\n\n本关已无法继续挑战。",
                trace_id
            )
            return
        
        # Check queue capacity
        if self.queue.is_full():
            await self._send_response(
                message,
                "⏳ 系统繁忙，请稍后再试...",
                trace_id
            )
            return
        
        # Set session to inflight atomically (check-and-set to prevent race)
        inflight_set = await self.storage.set_session_inflight(session.id)
        if not inflight_set:
            await self._send_response(message, "⏳ 你的上一个答案还在处理中，请稍候...", trace_id)
            return
        session.state = SessionState.INFLIGHT
        session.turn_count += 1
        session.last_attempt_at = datetime.now()
        await self.storage.upsert_session(session)
        
        # Create task and enqueue
        task = TaskPayload(
            trace_id=trace_id,
            user_id=user.id,
            telegram_user_id=message.user_id,
            chat_id=message.chat_id,
            message_id=message.message_id,
            username=message.username,
            level_id=current_level_id,
            session_id=session.id,
            user_prompt=message.text.strip(),
            turn_index=session.turn_count
        )
        
        success = await self.queue.enqueue(task)
        if not success:
            # Revert session state
            session.state = SessionState.READY
            session.turn_count -= 1
            await self.storage.upsert_session(session)
            await self._send_response(message, "⏳ 队列已满，请稍后再试...", trace_id)
            return
        
        # Send queue confirmation
        queue_size = self.queue.qsize()
        await self._send_response(
            message,
            f"✨ 收到你的答案！\n\n⏳ 已加入队列（前方约 {queue_size} 人）\n正在处理中，请稍候...",
            trace_id
        )
    
    async def process_task(self, task: TaskPayload) -> None:
        """Process a queued task (called by worker)."""
        trace_id = task.trace_id
        
        level_config = self.content.get_level(task.level_id)
        if not level_config:
            logger.error(f"Level {task.level_id} not found")
            return
        
        session = await self.storage.get_session(task.user_id, task.level_id)
        if not session:
            logger.error(f"Session not found for task {trace_id}")
            return
        
        try:
            try:
                # Call LLM generate (with per-level model if configured)
                llm_result = await self.llm_client.generate(
                    system_prompt=level_config.prompt.system_prompt,
                    user_prompt=task.user_prompt,
                    max_output_tokens=level_config.limits.max_output_tokens,
                    model=level_config.generate_model
                )
            except Exception as e:
                logger.exception(f"LLM generate error for task {trace_id}: {e}")
                llm_result = LLMResult(
                    output="",
                    model="unknown",
                    error=str(e)
                )
            
            # Log LLM call
            await self.storage.append_log_event(
                trace_id=trace_id,
                event_type=EventType.LLM_CALL,
                telegram_user_id=task.telegram_user_id,
                chat_id=task.chat_id,
                content=llm_result.output[:500],
                level_id=task.level_id,
                session_id=task.session_id,
                turn_index=task.turn_index,
                metadata={
                    'model': llm_result.model,
                    'tokens': llm_result.tokens_used,
                    'latency_ms': llm_result.latency_ms,
                    'error': llm_result.error
                }
            )
            
            if llm_result.error:
                # LLM error - don't count turn, set to ready
                await self._handle_llm_error(task, session, level_config, trace_id)
                return
            
            # Grade the output
            grade_result = await self.grader.grade(
                level_config=level_config,
                user_prompt=task.user_prompt,
                llm_output=llm_result.output
            )
            
            # Log grading
            await self.storage.append_log_event(
                trace_id=trace_id,
                event_type=EventType.GRADE,
                telegram_user_id=task.telegram_user_id,
                chat_id=task.chat_id,
                content=f"keyword={grade_result.keyword_verdict.value}, judge={grade_result.judge_verdict.value}, final={grade_result.final_verdict.value}",
                level_id=task.level_id,
                session_id=task.session_id,
                turn_index=task.turn_index,
                metadata={
                    'keyword_reason': grade_result.keyword_reason,
                    'judge_reason': grade_result.judge_reason,
                    'judge_parse_error': grade_result.judge_parse_error,
                    'judge_error': grade_result.judge_error
                }
            )

            if grade_result.judge_error:
                await self._handle_llm_error(task, session, level_config, trace_id)
                return
            
            # Record attempt
            attempt = Attempt(
                id=None,
                user_id=task.user_id,
                level_id=task.level_id,
                session_id=task.session_id,
                turn_index=task.turn_index,
                user_prompt=task.user_prompt,
                llm_output=llm_result.output,
                keyword_verdict=grade_result.keyword_verdict,
                judge_verdict=grade_result.judge_verdict,
                final_verdict=grade_result.final_verdict,
                grade_reason=f"{grade_result.keyword_reason}; {grade_result.judge_reason}"
            )
            await self.storage.record_attempt(attempt)
            
            # Handle result based on verdict
            if grade_result.final_verdict == GradeVerdict.PASS:
                await self._handle_pass(task, session, level_config, llm_result.output, trace_id)
            elif grade_result.final_verdict == GradeVerdict.SENSITIVE:
                await self._handle_sensitive(task, session, level_config, grade_result, trace_id)
            elif grade_result.judge_parse_error:
                await self._handle_judge_parse_error(task, session, level_config, trace_id)
            else:
                await self._handle_fail(task, session, level_config, llm_result.output, grade_result, trace_id)
        
        except Exception as e:
            logger.exception(f"Error processing task {trace_id}: {e}")
            # Reset session to ready on error
            session.state = SessionState.READY
            session.turn_count = max(0, session.turn_count - 1)  # Don't count errored attempt
            await self.storage.upsert_session(session)
            
            await self._send_to_user(
                task.chat_id,
                "❌ 处理出错，请稍后再试...",
                trace_id,
                task.telegram_user_id
            )
    
    async def _handle_pass(
        self,
        task: TaskPayload,
        session: Session,
        level_config: LevelConfig,
        llm_output: str,
        trace_id: str
    ) -> None:
        """Handle successful pass."""
        logger.info(f"[{trace_id}] 通关结果: user_id={task.user_id}, level={task.level_id}, result=PASS")
        
        # Mark level as passed
        await self.storage.mark_level_passed(task.user_id, task.level_id)
        
        # Update session
        session.state = SessionState.PASSED
        await self.storage.upsert_session(session)
        
        # Check if reward is enabled
        is_reward_active, _ = self._is_reward_enabled()
        
        # Build response message
        safe_output = self._escape_for_code_block(llm_output)
        response_parts = [
            f"🤖 回答：\n```\n{safe_output}\n```\n",
            f"\n通关情况：✅成功\n",
            "─" * 20,
            f"\n🎉 *恭喜通关第 {task.level_id} 关：{level_config.name}！*\n"
        ]
        
        # Only attempt reward when reward is active
        if is_reward_active:
            reward_pool = self.content.get_reward_pool(level_config.reward_pool_id)
            
            if not reward_pool or not reward_pool.enabled:
                response_parts.append("\n⚠️ 奖品池暂时关闭，请联系管理员。")
            else:
                claim_response = await self.storage.claim_reward(
                    pool_id=level_config.reward_pool_id,
                    user_id=task.user_id,
                    level_id=task.level_id
                )
                
                # Log reward claim
                await self.storage.append_log_event(
                    trace_id=trace_id,
                    event_type=EventType.REWARD_CLAIM,
                    telegram_user_id=task.telegram_user_id,
                    chat_id=task.chat_id,
                    content=f"result={claim_response.result.value}",
                    level_id=task.level_id,
                    session_id=task.session_id,
                    turn_index=task.turn_index,
                    metadata={
                        'pool_id': level_config.reward_pool_id,
                        'item_id': claim_response.item_id,
                        'result': claim_response.result.value
                    }
                )
                
                if claim_response.result == ClaimResult.SUCCESS and reward_pool:
                    reward_msg = reward_pool.send_message_template.format(
                        reward_code=claim_response.reward_code,
                        level_id=task.level_id,
                        level_name=level_config.name,
                        username=task.username or "玩家"
                    )
                    response_parts.append(f"\n{reward_msg}")
                elif claim_response.result == ClaimResult.ALREADY_CLAIMED:
                    response_parts.append("\n⚠️ 你已经领取过本关奖励了")
                elif claim_response.result == ClaimResult.NO_STOCK:
                    response_parts.append("\n😢 抱歉，奖品已发完")
        # When reward is not active, simply don't mention rewards at all
        
        # Check for next level
        levels = self.content.load_levels()
        next_level = None
        if task.level_id < len(levels):
            next_level = self.content.get_level(task.level_id + 1)
            if next_level:
                response_parts.append(f"\n\n🎮 下一关已解锁！")
        else:
            response_parts.append("\n\n🏆 *恭喜完成全部关卡！马年大吉！*")
        
        await self._send_to_user(
            task.chat_id,
            "".join(response_parts),
            trace_id,
            task.telegram_user_id
        )
        
        # Auto-send next level rules
        if next_level:
            await self._send_to_user(
                task.chat_id,
                next_level.prompt.intro_message,
                trace_id,
                task.telegram_user_id
            )
    
    async def _handle_fail(
        self,
        task: TaskPayload,
        session: Session,
        level_config: LevelConfig,
        llm_output: str,
        grade_result: GradeResult,
        trace_id: str
    ) -> None:
        """Handle failed attempt."""
        logger.info(f"[{trace_id}] 通关结果: user_id={task.user_id}, level={task.level_id}, result=FAIL")
        
        remaining_turns = TurnPolicy.get_remaining_turns(session, level_config.limits.max_turns)
        
        # Calculate cooldown
        cooldown_until = CooldownPolicy.calculate_cooldown_until(
            level_config.limits.cooldown_seconds_after_fail
        )
        
        # Update session
        if remaining_turns <= 0:
            session.state = SessionState.FAILED_OUT
        else:
            session.state = SessionState.COOLDOWN
            session.cooldown_until = cooldown_until
        
        await self.storage.upsert_session(session)
        
        # Build response
        safe_output = self._escape_for_code_block(llm_output)
        response_parts = [
            f"🤖 回答：\n```\n{safe_output}\n```\n",
            f"\n通关情况：❌失败\n",
            "─" * 20,
        ]
        
        # Add hint about why it failed (without revealing too much)
        if grade_result.keyword_verdict == GradeVerdict.FAIL:
            response_parts.append("\n💡 提示：回复中未包含目标短语\n")
        elif grade_result.judge_verdict == GradeVerdict.FAIL:
            response_parts.append("\n💡 提示：回复方式不符合要求（可能是拒绝或引用）\n")
        
        if remaining_turns > 0:
            cooldown_secs = level_config.limits.cooldown_seconds_after_fail
            response_parts.append(f"\n🔄 剩余 {remaining_turns} 次机会")
            response_parts.append(f"\n⏱️ 冷却时间：{cooldown_secs} 秒后可再次尝试")
        else:
            response_parts.append("\n😢 已用完所有机会，本关挑战失败")
        
        await self._send_to_user(
            task.chat_id,
            "".join(response_parts),
            trace_id,
            task.telegram_user_id
        )
    
    async def _handle_sensitive(
        self,
        task: TaskPayload,
        session: Session,
        level_config: LevelConfig,
        grade_result: GradeResult,
        trace_id: str
    ) -> None:
        """Handle sensitive content detected in LLM output."""
        logger.info(f"[{trace_id}] 通关结果: user_id={task.user_id}, level={task.level_id}, result=SENSITIVE")
        
        remaining_turns = TurnPolicy.get_remaining_turns(session, level_config.limits.max_turns)
        
        cooldown_until = CooldownPolicy.calculate_cooldown_until(
            level_config.limits.cooldown_seconds_after_fail
        )
        
        if remaining_turns <= 0:
            session.state = SessionState.FAILED_OUT
        else:
            session.state = SessionState.COOLDOWN
            session.cooldown_until = cooldown_until
        
        await self.storage.upsert_session(session)
        
        response_parts = [
            "🤖 回答：【已被屏蔽】\n",
            "\n通关情况：❌失败\n",
            "─" * 20,
        ]
        
        if remaining_turns > 0:
            cooldown_secs = level_config.limits.cooldown_seconds_after_fail
            response_parts.append(f"\n🔄 剩余 {remaining_turns} 次机会")
            response_parts.append(f"\n⏱️ 冷却时间：{cooldown_secs} 秒后可再次尝试")
        else:
            response_parts.append("\n😢 已用完所有机会，本关挑战失败")
        
        await self._send_to_user(
            task.chat_id,
            "".join(response_parts),
            trace_id,
            task.telegram_user_id
        )
    
    async def _handle_judge_parse_error(
        self,
        task: TaskPayload,
        session: Session,
        level_config: LevelConfig,
        trace_id: str
    ) -> None:
        """Handle judge parse error (verdict not PASS/FAIL/SENSITIVE)."""
        remaining_turns = TurnPolicy.get_remaining_turns(session, level_config.limits.max_turns)
        
        cooldown_until = CooldownPolicy.calculate_cooldown_until(
            level_config.limits.cooldown_seconds_after_fail
        )
        
        if remaining_turns <= 0:
            session.state = SessionState.FAILED_OUT
        else:
            session.state = SessionState.COOLDOWN
            session.cooldown_until = cooldown_until
        
        await self.storage.upsert_session(session)
        
        response_parts = [
            "🤖 回答：【系统识别失败】\n",
            "\n通关情况：❌失败\n",
            "─" * 20,
        ]
        
        if remaining_turns > 0:
            cooldown_secs = level_config.limits.cooldown_seconds_after_fail
            response_parts.append(f"\n🔄 剩余 {remaining_turns} 次机会")
            response_parts.append(f"\n⏱️ 冷却时间：{cooldown_secs} 秒后可再次尝试")
        else:
            response_parts.append("\n😢 已用完所有机会，本关挑战失败")
        
        await self._send_to_user(
            task.chat_id,
            "".join(response_parts),
            trace_id,
            task.telegram_user_id
        )
    
    async def _handle_llm_error(
        self,
        task: TaskPayload,
        session: Session,
        level_config: LevelConfig,
        trace_id: str
    ) -> None:
        """Handle LLM call error."""
        activity = self.content.load_activity()
        strategy = activity.judge_timeout_strategy
        
        if strategy == "fail_no_count":
            # Don't count the turn
            session.state = SessionState.READY
            session.turn_count = max(0, session.turn_count - 1)
            await self.storage.upsert_session(session)
            
            await self._send_to_user(
                task.chat_id,
                "⚠️ AI 响应超时，请稍后再试（本次不计入尝试次数）",
                trace_id,
                task.telegram_user_id
            )
        else:
            # Count as failure
            remaining = TurnPolicy.get_remaining_turns(session, level_config.limits.max_turns)
            cooldown_until = CooldownPolicy.calculate_cooldown_until(
                level_config.limits.cooldown_seconds_after_fail
            )
            
            session.state = SessionState.COOLDOWN if remaining > 0 else SessionState.FAILED_OUT
            session.cooldown_until = cooldown_until
            await self.storage.upsert_session(session)
            
            await self._send_to_user(
                task.chat_id,
                f"⚠️ AI 响应出错，本次判定为失败\n🔄 剩余 {remaining} 次机会",
                trace_id,
                task.telegram_user_id
            )
    
    async def _check_session_state(
        self,
        session: Session,
        level_config: LevelConfig,
        user_id: int
    ) -> Optional[str]:
        """Check session state and return error message if can't proceed."""
        if session.state == SessionState.INFLIGHT:
            return "⏳ 你的上一个答案还在处理中，请稍候..."
        
        if session.state == SessionState.PASSED:
            return "✅ 你已经通关这一关了！发送 /status 查看下一关"
        
        if session.state == SessionState.FAILED_OUT:
            return "❌ 本关已无法继续挑战（已用完所有机会）"
        
        if session.state == SessionState.COOLDOWN:
            is_cooling, remaining = CooldownPolicy.is_in_cooldown(session)
            if is_cooling and remaining:
                return f"⏱️ 冷却中，请等待 {remaining} 秒后再试"
            else:
                # Cooldown ended, update state
                session.state = SessionState.READY
                session.cooldown_until = None
                await self.storage.upsert_session(session)
        
        return None
    
    async def _all_levels_passed(self, user_id: int, total_levels: int) -> bool:
        """Check if user has passed all levels."""
        for level_id in range(1, total_levels + 1):
            if not await self.storage.is_level_passed(user_id, level_id):
                return False
        return True
    
    @staticmethod
    def _escape_for_code_block(text: str) -> str:
        """Escape text for safe inclusion in a Markdown code block."""
        return text.replace("```", "'''")
    
    async def _send_response(self, message: Message, text: str, trace_id: str) -> None:
        """Send response and log it."""
        await self.channel.reply_to(message.chat_id, message.message_id, text)
        await self.storage.append_log_event(
            trace_id=trace_id,
            event_type=EventType.SYSTEM_OUT,
            telegram_user_id=message.user_id,
            chat_id=message.chat_id,
            content=text
        )
    
    async def _send_to_user(
        self,
        chat_id: int,
        text: str,
        trace_id: str,
        telegram_user_id: int
    ) -> None:
        """Send message to user and log it."""
        await self.channel.send_text(chat_id, text)
        await self.storage.append_log_event(
            trace_id=trace_id,
            event_type=EventType.SYSTEM_OUT,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            content=text
        )
    
    # ==================== Command Handlers ====================
    
    async def _check_activity_active(self, message: Message, trace_id: str) -> bool:
        """Check if activity is active. Returns True if active, False if not (and sends response)."""
        is_active, reason = self._is_activity_enabled()
        if not is_active:
            await self._send_response(message, reason, trace_id)
            return False
        return True

    async def handle_start(self, message: Message) -> None:
        """Handle /start command."""
        trace_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{trace_id}] 用户发送指令: user_id={message.user_id}, nickname={message.nickname}, command=/start")
        
        if not await self._check_activity_active(message, trace_id):
            return
        
        user = await self.storage.get_or_create_user(
            telegram_user_id=message.user_id,
            username=message.username
        )
        
        activity = self.content.load_activity()
        levels = self.content.load_levels()
        current_level = await self.storage.get_current_level(user.id, len(levels))
        
        welcome_text = f"""🐴 *欢迎来到 {activity.title}！*

🎮 *玩法说明*
- 共有 {len(levels)} 个关卡，需按顺序挑战
- 每关发送你的 prompt，让 AI 输出指定内容
- 通关后自动发放奖励

📍 *当前进度*
- 当前关卡：第 {current_level} 关
- 发送 /status 查看详细状态
- 发送 /rules 查看当前关卡规则

💡 *提示*
直接发送文字即可开始答题！更多活动相关信息请前往 @ShubiChannel 查看。

祝你马年大吉，一路通关！🎉"""
        
        await self._send_response(message, welcome_text, trace_id)
        
        # If at first level, also send level intro
        if current_level <= len(levels):
            level = self.content.get_level(current_level)
            if level:
                await self._send_response(message, level.prompt.intro_message, trace_id)
    
    async def handle_help(self, message: Message) -> None:
        """Handle /help command."""
        trace_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{trace_id}] 用户发送指令: user_id={message.user_id}, command=/help")
        
        if not await self._check_activity_active(message, trace_id):
            return
        
        help_text = """📖 **帮助说明**

🎯 **目标**
让 AI 在回复中输出指定的目标短语

⚠️ **注意事项**
- AI 可能会拒绝直接输出，需要巧妙构造 prompt
- 不能通过"请说 XXX"这样直接的方式
- 需要让 AI 自然地生成包含目标的内容

📝 **命令列表**
/start - 开始游戏
/status - 查看当前状态
/rules - 查看当前关卡规则
/help - 显示帮助

🎁 **奖励说明**
- 第 1 关：100份共计100元支付宝拼手气口令红包
- 第 2 关：50份共计100元支付宝拼手气口令红包
- 第 3 关：20份共计100元支付宝拼手气口令红包
- 第 4 关：支付宝口令红包20元×5（限5人领取）
- 第 5 关：支付宝口令红包50元×1（限1人领取）

❓ **常见问题**
Q: 为什么包含关键词还是失败？
A: AI 可能是在"拒绝"的语境下提到关键词

Q: 冷却时间是什么？
A: 失败后需等待几秒才能再次尝试

💡 更多活动相关信息请前往 @ShubiChannel 查看"""
        
        await self._send_response(message, help_text, trace_id)
    
    async def handle_status(self, message: Message) -> None:
        """Handle /status command."""
        trace_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{trace_id}] 用户发送指令: user_id={message.user_id}, command=/status")
        
        if not await self._check_activity_active(message, trace_id):
            return
        
        user = await self.storage.get_or_create_user(
            telegram_user_id=message.user_id,
            username=message.username
        )
        
        levels = self.content.load_levels()
        current_level_id = await self.storage.get_current_level(user.id, len(levels))
        progress = await self.storage.get_user_progress(user.id)
        
        passed_set = {p.level_id for p in progress if p.passed}
        
        status_lines = ["📊 *你的游戏状态*\n"]
        
        for level in levels:
            if level.level_id in passed_set:
                status_lines.append(f"✅ 第 {level.level_id} 关：{level.name}")
            elif level.level_id == current_level_id:
                session = await self.storage.get_session(user.id, level.level_id)
                turns_used = session.turn_count if session else 0
                max_turns = level.limits.max_turns
                status_lines.append(f"🎮 第 {level.level_id} 关：{level.name}（进行中 {turns_used}/{max_turns}）")
            else:
                status_lines.append(f"🔒 第 {level.level_id} 关：{level.name}")
        
        if current_level_id > len(levels):
            status_lines.append("\n🏆 *恭喜！已通关全部关卡！*")
        else:
            status_lines.append(f"\n💡 发送文字答案挑战第 {current_level_id} 关")
        
        await self._send_response(message, "\n".join(status_lines), trace_id)
    
    async def handle_rules(self, message: Message) -> None:
        """Handle /rules command."""
        trace_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{trace_id}] 用户发送指令: user_id={message.user_id}, command=/rules")
        
        if not await self._check_activity_active(message, trace_id):
            return
        
        user = await self.storage.get_or_create_user(
            telegram_user_id=message.user_id,
            username=message.username
        )
        
        levels = self.content.load_levels()
        current_level_id = await self.storage.get_current_level(user.id, len(levels))
        
        if current_level_id > len(levels):
            await self._send_response(
                message,
                "🏆 你已通关全部关卡！",
                trace_id
            )
            return
        
        level = self.content.get_level(current_level_id)
        if not level:
            await self._send_response(message, "关卡信息不可用", trace_id)
            return
        
        rules_text = f"""📋 *第 {level.level_id} 关规则*

{level.prompt.intro_message}

⚙️ **限制条件**
- 输入长度：最多 {level.limits.max_input_chars} 字符
- 尝试次数：最多 {level.limits.max_turns} 次
- 失败冷却：{level.limits.cooldown_seconds_after_fail} 秒"""
        
        await self._send_response(message, rules_text, trace_id)

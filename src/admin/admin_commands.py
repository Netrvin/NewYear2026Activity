"""Admin commands for managing the activity."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from ..domain.models import Message, EventType
from ..domain.policies import ActivityTimePolicy, RewardTimePolicy
from ..ports.storage import IStorage
from ..ports.content import IContentProvider
from ..ports.channel import IChannelAdapter
from ..ports.queue import IQueue
from ..workers.worker import WorkerPool

logger = logging.getLogger(__name__)


class AdminCommands:
    """Handler for admin commands."""
    
    def __init__(
        self,
        admin_user_ids: List[int],
        storage: IStorage,
        content: IContentProvider,
        channel: IChannelAdapter,
        queue: IQueue,
        worker_pool: Optional[WorkerPool] = None
    ):
        self.admin_user_ids = set(admin_user_ids)
        self.storage = storage
        self.content = content
        self.channel = channel
        self.queue = queue
        self.worker_pool = worker_pool
        
        # Toggle override: None = no override (use time), True = force on, False = force off
        self._toggle_override: Optional[bool] = None
        # Reward toggle override: None = no override (use time), True = force on, False = force off
        self._reward_toggle_override: Optional[bool] = None
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is an admin."""
        return user_id in self.admin_user_ids
    
    async def handle_admin_command(self, message: Message) -> None:
        """Route admin commands."""
        if not self.is_admin(message.user_id):
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 你没有管理员权限"
            )
            return
        
        # Parse command
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            await self._send_admin_help(message)
            return
        
        subcommand = parts[1].lower()
        args = parts[2] if len(parts) > 2 else ""
        
        # Route to appropriate handler
        handlers = {
            'ping': self._handle_ping,
            'toggle': self._handle_toggle,
            'togglereward': self._handle_toggle_reward,
            'reload_config': self._handle_reload_config,
            'reload': self._handle_reload_config,
            'stats': self._handle_stats,
            'user': self._handle_user,
            'ban': self._handle_ban,
            'unban': self._handle_unban,
            'export_logs': self._handle_export_logs,
            'export': self._handle_export_logs,
            'reset_level': self._handle_reset_level,
            'clear_queue': self._handle_clear_queue,
            'help': self._send_admin_help,
        }
        
        handler = handlers.get(subcommand)
        if handler:
            await handler(message, args)
        else:
            await self._send_admin_help(message)
    
    async def _send_admin_help(self, message: Message, args: str = "") -> None:
        """Send admin help message."""
        help_text = """🔧 **管理员命令**

/admin ping - 健康检查
/admin toggle none|on|off - 活动覆写（none=按时间/on=强制开/off=强制关）
/admin togglereward none|on|off - 发奖覆写（none=按时间/on=强制开/off=强制关）
/admin reload_config - 重载配置
/admin stats - 查看统计
/admin user <telegram_id> - 查看用户
/admin ban <telegram_id> [reason] - 封禁用户
/admin unban <telegram_id> - 解封用户
/admin reset_level <telegram_id> <level_id> - 重置用户关卡
/admin clear_queue - 清空处理队列
/admin export_logs [YYYY-MM-DD] - 导出日志"""
        
        await self.channel.reply_to(message.chat_id, message.message_id, help_text)
    
    async def _handle_ping(self, message: Message, args: str) -> None:
        """Health check."""
        try:
            # Check database
            stats = await self.storage.get_stats()
            db_status = "✅"
        except Exception as e:
            db_status = f"❌ {e}"
        
        # Check queue
        queue_size = self.queue.qsize()
        
        # Check workers
        active_workers = self.worker_pool.active_workers if self.worker_pool else 0
        total_workers = self.worker_pool.concurrency if self.worker_pool else 0
        
        # Toggle override status
        if self._toggle_override is None:
            override_status = '🔄 none（按时间自动）'
        elif self._toggle_override:
            override_status = '✅ on（强制开启）'
        else:
            override_status = '❌ off（强制关闭）'
        
        # Time-based activity status
        activity = self.content.load_activity()
        is_time_active, time_reason = ActivityTimePolicy.is_activity_active(activity)
        if is_time_active:
            time_status = f"✅ 进行中 ({activity.start_at.strftime('%H:%M')}—{activity.end_at.strftime('%H:%M')})"
        else:
            now = datetime.now(activity.start_at.tzinfo)
            if now < activity.start_at:
                time_status = f"⏳ 未开始 ({activity.start_at.strftime('%Y-%m-%d %H:%M')} 开始)"
            elif now > activity.end_at:
                time_status = "⏹️ 已结束"
            else:
                time_status = f"❌ {time_reason}"
        
        # Effective status
        is_effective, _ = self.get_activity_status()
        effective_status = '✅ 开启' if is_effective else '❌ 关闭'
        
        # Reward override status
        if self._reward_toggle_override is None:
            reward_override_status = '🔄 none（按时间自动）'
        elif self._reward_toggle_override:
            reward_override_status = '✅ on（强制开启）'
        else:
            reward_override_status = '❌ off（强制关闭）'
        
        # Time-based reward status
        is_reward_time_active, reward_time_reason = RewardTimePolicy.is_reward_active(activity)
        reward_start = activity.reward_start_at or activity.start_at
        reward_end = activity.reward_end_at or activity.end_at
        if is_reward_time_active:
            reward_time_status = f"✅ 进行中 ({reward_start.strftime('%H:%M')}—{reward_end.strftime('%H:%M')})"
        else:
            now = datetime.now(reward_start.tzinfo)
            if now < reward_start:
                reward_time_status = f"⏳ 未开始 ({reward_start.strftime('%Y-%m-%d %H:%M')} 开始)"
            elif now > reward_end:
                reward_time_status = "⏹️ 已结束"
            else:
                reward_time_status = f"❌ {reward_time_reason}"
        
        # Effective reward status
        is_reward_effective, _ = self.get_reward_status()
        reward_effective_status = '✅ 开启' if is_reward_effective else '❌ 关闭'
        
        response = f"""🏥 **健康检查**

📦 数据库：{db_status}
📬 队列长度：{queue_size}
👷 Worker：{active_workers}/{total_workers} 活跃
🎮 活动覆写：{override_status}
⏰ 活动时间：{time_status}
📡 生效状态：{effective_status}
🎁 发奖覆写：{reward_override_status}
🕐 发奖时间：{reward_time_status}
💰 发奖生效：{reward_effective_status}"""
        
        await self.channel.reply_to(message.chat_id, message.message_id, response)
    
    async def _handle_toggle(self, message: Message, args: str) -> None:
        """Toggle activity override: none (use time), on (force), off (force)."""
        args = args.strip().lower()
        
        if args == "on":
            self._toggle_override = True
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "✅ 活动已强制开启（忽略时间窗口）"
            )
        elif args == "off":
            self._toggle_override = False
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 活动已强制关闭"
            )
        elif args == "none":
            self._toggle_override = None
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "🔄 已取消覆写，活动状态将按时间窗口自动判断"
            )
        else:
            if self._toggle_override is None:
                current = "none（按时间自动）"
            elif self._toggle_override:
                current = "on（强制开启）"
            else:
                current = "off（强制关闭）"
            is_effective, _ = self.get_activity_status()
            effective = "开启" if is_effective else "关闭"
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"当前覆写：{current}\n生效状态：{effective}\n\n用法：/admin toggle none|on|off"
            )
    
    async def _handle_reload_config(self, message: Message, args: str) -> None:
        """Reload configuration files."""
        try:
            self.content.reload()
            
            # Validate
            errors = self.content.validate()
            if errors:
                await self.channel.reply_to(
                    message.chat_id,
                    message.message_id,
                    f"⚠️ 配置重载成功但有警告：\n" + "\n".join(errors)
                )
            else:
                # Sync reward items
                rewards = self.content.load_rewards()
                await self.storage.sync_reward_items([
                    {
                        'pool_id': r.pool_id,
                        'items': [
                            {
                                'item_id': i.item_id,
                                'type': i.type,
                                'code': i.code,
                                'max_claims_per_item': i.max_claims_per_item
                            }
                            for i in r.items
                        ]
                    }
                    for r in rewards
                ])
                
                await self.channel.reply_to(
                    message.chat_id,
                    message.message_id,
                    "✅ 配置已重载"
                )
        except Exception as e:
            logger.exception(f"Failed to reload config: {e}")
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"❌ 配置重载失败：{e}"
            )
    
    async def _handle_stats(self, message: Message, args: str) -> None:
        """Show statistics."""
        try:
            stats = await self.storage.get_stats()
            
            # Build response
            lines = [
                "📊 **活动统计**\n",
                f"👥 总用户数：{stats['total_users']}",
                f"📝 今日请求：{stats['today_attempts']}",
                f"🎁 今日发奖：{stats['today_claims']}",
                f"📬 队列长度：{self.queue.qsize()}",
                "\n**通关人数（按关卡）**"
            ]
            
            for level_id, count in sorted(stats.get('passed_by_level', {}).items()):
                lines.append(f"  第 {level_id} 关：{count} 人")
            
            lines.append("\n**奖品库存**")
            for pool_id, stock in stats.get('reward_stock', {}).items():
                lines.append(f"  {pool_id}：{stock['remaining']}/{stock['total']}")
            
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "\n".join(lines)
            )
        except Exception as e:
            logger.exception(f"Failed to get stats: {e}")
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"❌ 获取统计失败：{e}"
            )
    
    async def _handle_user(self, message: Message, args: str) -> None:
        """View user status."""
        if not args.strip():
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "用法：/admin user <telegram_user_id>"
            )
            return
        
        try:
            telegram_user_id = int(args.strip())
        except ValueError:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 无效的用户 ID"
            )
            return
        
        user = await self.storage.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 用户不存在"
            )
            return
        
        levels = self.content.load_levels()
        progress = await self.storage.get_user_progress(user.id)
        claims = await self.storage.get_user_claims(user.id)
        current_level = await self.storage.get_current_level(user.id, len(levels))
        
        passed_set = {p.level_id for p in progress if p.passed}
        
        lines = [
            f"👤 **用户信息**\n",
            f"Telegram ID：{user.telegram_user_id}",
            f"用户名：@{user.username or '无'}",
            f"封禁状态：{'🚫 已封禁' if user.is_banned else '✅ 正常'}",
            f"注册时间：{user.created_at.strftime('%Y-%m-%d %H:%M')}",
            f"\n**关卡进度**",
            f"当前关卡：第 {current_level} 关",
            f"已通关：{', '.join(str(l) for l in sorted(passed_set)) or '无'}",
            f"\n**领奖记录**"
        ]
        
        if claims:
            for claim in claims:
                lines.append(f"  第 {claim.level_id} 关：{claim.pool_id}")
        else:
            lines.append("  无")
        
        await self.channel.reply_to(message.chat_id, message.message_id, "\n".join(lines))
    
    async def _handle_ban(self, message: Message, args: str) -> None:
        """Ban a user."""
        parts = args.strip().split(maxsplit=1)
        if not parts:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "用法：/admin ban <telegram_user_id> [reason]"
            )
            return
        
        try:
            telegram_user_id = int(parts[0])
        except ValueError:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 无效的用户 ID"
            )
            return
        
        reason = parts[1] if len(parts) > 1 else "管理员封禁"
        
        success = await self.storage.update_user_ban_status(
            telegram_user_id=telegram_user_id,
            is_banned=True,
            reason=reason
        )
        
        if success:
            # Log the action
            await self.storage.append_log_event(
                trace_id=str(uuid.uuid4())[:8],
                event_type=EventType.SYSTEM_OUT,
                telegram_user_id=message.user_id,
                chat_id=message.chat_id,
                content=f"Admin banned user {telegram_user_id}: {reason}"
            )
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"✅ 已封禁用户 {telegram_user_id}"
            )
        else:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 用户不存在"
            )
    
    async def _handle_unban(self, message: Message, args: str) -> None:
        """Unban a user."""
        if not args.strip():
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "用法：/admin unban <telegram_user_id>"
            )
            return
        
        try:
            telegram_user_id = int(args.strip())
        except ValueError:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 无效的用户 ID"
            )
            return
        
        success = await self.storage.update_user_ban_status(
            telegram_user_id=telegram_user_id,
            is_banned=False,
            reason=None
        )
        
        if success:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"✅ 已解封用户 {telegram_user_id}"
            )
        else:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 用户不存在"
            )
    
    async def _handle_export_logs(self, message: Message, args: str) -> None:
        """Export logs."""
        date_filter = None
        if args.strip():
            try:
                date_filter = datetime.strptime(args.strip(), "%Y-%m-%d")
            except ValueError:
                await self.channel.reply_to(
                    message.chat_id,
                    message.message_id,
                    "❌ 日期格式无效，请使用 YYYY-MM-DD"
                )
                return
        
        try:
            logs = await self.storage.export_logs(date=date_filter, mask_codes=True)
            
            if not logs:
                await self.channel.reply_to(
                    message.chat_id,
                    message.message_id,
                    "📭 没有找到日志记录"
                )
                return
            
            # Format as JSONL
            jsonl_content = "\n".join(json.dumps(log, ensure_ascii=False) for log in logs)
            
            # For now, send a summary (full export would need file upload)
            date_str = date_filter.strftime("%Y-%m-%d") if date_filter else "全部"
            summary = f"""📤 **日志导出**

日期范围：{date_str}
总记录数：{len(logs)}

最近 5 条记录：
"""
            for log in logs[-5:]:
                summary += f"\n[{log['event_type']}] {log['content'][:50]}..."
            
            summary += f"\n\n完整日志包含 {len(jsonl_content)} 字符"
            
            await self.channel.reply_to(message.chat_id, message.message_id, summary)
            
        except Exception as e:
            logger.exception(f"Failed to export logs: {e}")
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"❌ 导出失败：{e}"
            )
    
    @property
    def toggle_override(self) -> Optional[bool]:
        """Current toggle override state: None/True/False."""
        return self._toggle_override

    @property
    def reward_toggle_override(self) -> Optional[bool]:
        """Current reward toggle override state: None/True/False."""
        return self._reward_toggle_override

    def is_activity_enabled(self) -> bool:
        """Check if activity is effectively enabled (combining override + time)."""
        is_enabled, _ = self.get_activity_status()
        return is_enabled

    def get_activity_status(self) -> tuple[bool, str]:
        """Get effective activity status as (enabled, reason) for engine integration.
        
        Logic:
        - override=True  → always enabled (force on)
        - override=False → always disabled (force off)
        - override=None  → determined by ActivityTimePolicy (time window)
        """
        if self._toggle_override is True:
            return True, "活动已开启（管理员强制开启）"
        if self._toggle_override is False:
            return False, "⏸️ 活动暂时关闭（管理员强制关闭）"
        # No override → use time policy
        activity = self.content.load_activity()
        return ActivityTimePolicy.is_activity_active(activity)

    def is_reward_enabled(self) -> bool:
        """Check if reward is effectively enabled (combining override + time)."""
        is_enabled, _ = self.get_reward_status()
        return is_enabled

    def get_reward_status(self) -> tuple[bool, str]:
        """Get effective reward status as (enabled, reason).

        Logic:
        - override=True  → always enabled (force on)
        - override=False → always disabled (force off)
        - override=None  → determined by RewardTimePolicy (time window)
        """
        if self._reward_toggle_override is True:
            return True, "发奖已开启（管理员强制开启）"
        if self._reward_toggle_override is False:
            return False, "发奖已关闭（管理员强制关闭）"
        # No override → use time policy
        activity = self.content.load_activity()
        return RewardTimePolicy.is_reward_active(activity)

    async def _handle_toggle_reward(self, message: Message, args: str) -> None:
        """Toggle reward override: none (use time), on (force), off (force)."""
        args = args.strip().lower()

        if args == "on":
            self._reward_toggle_override = True
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "✅ 发奖已强制开启（忽略时间窗口）"
            )
        elif args == "off":
            self._reward_toggle_override = False
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 发奖已强制关闭"
            )
        elif args == "none":
            self._reward_toggle_override = None
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "🔄 已取消发奖覆写，发奖状态将按时间窗口自动判断"
            )
        else:
            if self._reward_toggle_override is None:
                current = "none（按时间自动）"
            elif self._reward_toggle_override:
                current = "on（强制开启）"
            else:
                current = "off（强制关闭）"
            is_reward_effective, _ = self.get_reward_status()
            effective = "开启" if is_reward_effective else "关闭"
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"当前发奖覆写：{current}\n发奖生效状态：{effective}\n\n用法：/admin togglereward none|on|off"
            )

    async def _handle_reset_level(self, message: Message, args: str) -> None:
        """Reset a user's session for a specific level."""
        parts = args.strip().split()
        if len(parts) < 2:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "用法：/admin reset_level <telegram_user_id> <level_id>"
            )
            return
        
        try:
            telegram_user_id = int(parts[0])
            level_id = int(parts[1])
        except ValueError:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 无效的参数，请提供数字类型的用户 ID 和关卡 ID"
            )
            return
        
        user = await self.storage.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                "❌ 用户不存在"
            )
            return
        
        # Reset session
        success = await self.storage.reset_session(user.id, level_id)
        
        # Log the action
        await self.storage.append_log_event(
            trace_id=str(uuid.uuid4())[:8],
            event_type=EventType.SYSTEM_OUT,
            telegram_user_id=message.user_id,
            chat_id=message.chat_id,
            content=f"Admin reset level {level_id} for user {telegram_user_id}"
        )
        
        if success:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"✅ 已重置用户 {telegram_user_id} 的第 {level_id} 关会话"
            )
        else:
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"⚠️ 用户 {telegram_user_id} 没有第 {level_id} 关的会话记录"
            )
    
    async def _handle_clear_queue(self, message: Message, args: str) -> None:
        """Clear the processing queue."""
        try:
            size_before = self.queue.qsize()
            await self.queue.clear()
            
            await self.storage.append_log_event(
                trace_id=str(uuid.uuid4())[:8],
                event_type=EventType.SYSTEM_OUT,
                telegram_user_id=message.user_id,
                chat_id=message.chat_id,
                content=f"Admin cleared queue ({size_before} tasks removed)"
            )
            
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"✅ 队列已清空（移除了 {size_before} 个任务）"
            )
        except Exception as e:
            logger.exception(f"Failed to clear queue: {e}")
            await self.channel.reply_to(
                message.chat_id,
                message.message_id,
                f"❌ 清空队列失败：{e}"
            )

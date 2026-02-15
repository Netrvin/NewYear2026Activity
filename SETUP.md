# 2026 马年·极客闯关 Bot

> Telegram Bot 闯关活动系统 - Python + SQLite/MySQL

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写配置：

```bash
copy .env.example .env
```

编辑 `.env` 文件：

```env
# Telegram Bot Token (从 @BotFather 获取)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# OpenAI API 配置
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 管理员用户 ID（逗号分隔的 Telegram 用户 ID）
ADMIN_USER_IDS=123456789,987654321

# 数据库后端：sqlite 或 mysql
DATABASE_BACKEND=sqlite

# SQLite 配置（DATABASE_BACKEND=sqlite 时使用）
DATABASE_PATH=data/activity.db

# MySQL 8.4.8 配置（DATABASE_BACKEND=mysql 时使用）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=new_year_2026_activity
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password_here
MYSQL_CHARSET=utf8mb4
MYSQL_CONNECT_TIMEOUT=5
MYSQL_POOL_MIN_SIZE=1
MYSQL_POOL_MAX_SIZE=10

# 日志级别
LOG_LEVEL=INFO
```

### 3. 启动服务

```bash
# 正常启动
python -m src.app.main

# 使用 Mock LLM 测试（不调用真实 API）
python -m src.app.main --mock
```

## 项目结构

```
NewYear2026Activity/
├── config/                    # JSON 配置文件
│   ├── activity.json         # 活动总配置
│   ├── levels.json           # 关卡配置（5 关）
│   └── rewards.json          # 奖品池配置
├── data/                      # 数据目录（自动创建）
│   └── activity.db           # SQLite 数据库
├── src/
│   ├── app/
│   │   ├── main.py           # 主程序入口
│   │   ├── container.py      # 依赖注入容器
│   │   └── settings.py       # 环境配置
│   ├── domain/
│   │   ├── models.py         # 领域模型
│   │   ├── engine.py         # 游戏引擎（核心状态机）
│   │   ├── grading.py        # 判题模块
│   │   └── policies.py       # 业务策略
│   ├── ports/                 # 接口定义（可替换点）
│   │   ├── channel.py        # 消息渠道接口
│   │   ├── storage.py        # 存储接口
│   │   ├── llm.py            # LLM 接口
│   │   ├── content.py        # 内容提供者接口
│   │   └── queue.py          # 队列接口
│   ├── adapters/              # 接口实现
│   │   ├── telegram/         # Telegram 适配器
│   │   ├── storage_sqlite/   # SQLite 存储
│   │   ├── storage_mysql/    # MySQL 存储
│   │   ├── llm_openai_compat/# OpenAI 兼容客户端
│   │   ├── content_json/     # JSON 配置加载
│   │   └── queue_memory/     # 队列（内存 + 持久化）
│   │       ├── memory_queue.py      # 纯内存队列
│   │       └── persistent_queue.py  # 带持久化的队列
│   ├── workers/
│   │   └── worker.py         # Worker 池
│   ├── admin/
│   │   └── admin_commands.py # 管理员命令
│   └── tests/                 # 测试
├── scripts/
│   └── stress_test.py        # 并发压测脚本
├── requirements.txt
├── pytest.ini
└── DEMAND.md
```

## 用户命令

| 命令 | 说明 |
|------|------|
| `/start` | 开始游戏，显示欢迎信息和当前关卡 |
| `/help` | 显示帮助信息 |
| `/status` | 查看当前进度和状态 |
| `/rules` | 显示当前关卡的规则 |

## 管理员命令

使用 `/admin <命令>` 格式：

| 命令 | 说明 |
|------|------|
| `/admin ping` | 健康检查 |
| `/admin toggle none\|on\|off` | 活动覆写（none=按时间/on=强制开/off=强制关） |
| `/admin togglereward none\|on\|off` | 发奖覆写（none=按时间/on=强制开/off=强制关） |
| `/admin reload_config` | 重载配置文件 |
| `/admin stats` | 查看统计数据 |
| `/admin user <id>` | 查看用户信息 |
| `/admin ban <id> [reason]` | 封禁用户 |
| `/admin unban <id>` | 解封用户 |
| `/admin reset_level <id> <level>` | 重置用户指定关卡会话 |
| `/admin clear_queue` | 清空处理队列 |
| `/admin export_logs [date]` | 导出日志 |

## 运行测试

```bash
# 运行所有测试
pytest

# 运行带覆盖率
pytest --cov=src

# 运行特定测试
pytest src/tests/test_grading.py -v

# 运行队列持久化测试
pytest src/tests/test_queue.py -v

# 运行存储测试（包含队列持久化）
pytest src/tests/test_storage.py -v
```

## 并发压测

```bash
# 默认参数（50 用户，10 张 E 卡）
python scripts/stress_test.py

# 自定义参数
python scripts/stress_test.py <num_users> <num_ecards> <concurrent_workers>
python scripts/stress_test.py 100 20 30
```

## 配置说明

### activity.json

```json
{
  "activity_id": "horse_2026_geek_v1",
  "title": "2026 马年·极客闯关",
  "enabled": true,
  "start_at": "2026-02-10T00:00:00+08:00",
  "end_at": "2026-03-01T00:00:00+08:00",
  "global_limits": {
    "max_inflight_per_user": 1,
    "queue_max_length": 20000,
    "worker_concurrency": 8
  },
  "llm": {
    "provider": "openai_compatible",
    "model": "gpt-4o-mini",
    "timeout_seconds": 30
  }
}
```

### levels.json

每个关卡配置包含：
- `level_id`: 关卡 ID（从 1 开始连续）
- `name`: 关卡名称
- `prompt.system_prompt`: 给 LLM 的系统提示
- `prompt.intro_message`: 给用户的关卡说明
- `limits`: 输入长度、尝试次数、冷却时间、输出 token 限制
- `grading`: 判题配置（关键词 + Judge）
- `reward_pool_id`: 关联的奖品池

### rewards.json

奖品池配置：
- `ALIPAY_CODE`: 支付宝口令红包，`max_claims_per_item` 可大于 1
- `JD_ECARD`: 京东 E 卡，`max_claims_per_item` 必须为 1

## 架构设计

### 三大解耦模块

1. **消息渠道（IChannelAdapter）**
   - 当前实现：Telegram
   - 可替换为：Discord、企业微信、Web 等

2. **数据存储（IStorage）**
   - 当前实现：SQLite
   - 可替换为：PostgreSQL、MySQL、Redis 等

3. **内容配置（IContentProvider）**
   - 当前实现：JSON 文件
   - 可替换为：数据库、远程配置等

### 判题流程

1. **关键词判题**：检查 LLM 输出是否包含目标短语
2. **LLM Judge 判题**：判断是否为"自然产出"而非"拒绝引用"
3. **最终结果**：两者都 PASS 才通关

### 并发控制

- 内存队列 + Worker 池
- 用户 inflight 锁（同一用户同时只能有一个处理中的请求）
- SQLite WAL 模式 + busy_timeout
- 原子化奖品领取（防超发）

### 队列持久化

系统支持队列任务持久化，确保程序意外重启后不会丢失用户提交的请求：

- **持久化时机**：用户提交 prompt 入队时，同时写入 SQLite 的 `pending_tasks` 表
- **恢复机制**：程序启动时自动从数据库恢复未处理的任务到内存队列
- **顺序保证**：恢复时按 `enqueued_at` 时间排序，先提交的先处理
- **完成清理**：Worker 处理完成后，从持久化存储中删除该任务

队列持久化确保了：
1. 程序崩溃/重启不丢失用户请求
2. 用户按提交顺序处理
3. 不会重复处理已完成的任务

## 故障排查

### 常见问题

1. **Bot 无法启动**
   - 检查 `TELEGRAM_BOT_TOKEN` 是否正确
   - 检查网络连接

2. **LLM 调用失败**
   - 检查 `OPENAI_API_KEY` 是否正确
   - 检查 API 配额

3. **数据库锁定**
   - 确保只有一个进程在运行
   - 检查 `data/` 目录权限

### 日志查看

日志默认输出到控制台，可通过 `LOG_LEVEL` 环境变量调整级别。

## License

MIT


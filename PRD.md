# 2026 马年·极客闯关 Bot（PRD.md）

这是你需要逐项完成的任务，详细设计文档参考 `DEMAND.md`。

## Overview
开发一个可复用的闯关活动系统（Python + SQLite），当前接入 Telegram Bot。用户按顺序挑战 5 个关卡：系统下发关卡说明，用户提交 prompt，系统调用大模型生成回答，并用“关键词判题 + 大模型 Judge 判题”双重判定是否通关；通关后按 JSON 配置发放奖品（支付宝口令红包/京东E卡）。系统需支持高并发排队、限频防刷、事务一致性、全量交互日志审计，并保证题目/渠道/存储三大模块解耦可替换。

---

## Goals / 非目标
### Goals
- 题目获取/判题、消息渠道（Telegram）、数据读写（SQLite）三模块彻底解耦（接口隔离）。
- 全部活动配置、关卡、奖品信息来自 JSON 文件，可管理员命令 reload。
- 5 个关卡（马年 2026 极客风）可顺序挑战，支持输入长度、轮次、冷却、max_output_tokens 控制。
- 双重判题：关键词命中 + LLM Judge 通过，且能识别“我不能说xxxx”这类非本意命中。
- 奖品：支付宝口令红包可多人共享同口令（可配置 max_claims_per_item），京东E卡一人一张（强制=1）。
- 高并发：任务入队 + worker 并发可控；同用户 inflight 锁；失败后冷却。
- SQLite 并发正确：WAL、busy_timeout、短事务；claim_reward 原子一致，不超发不重复发。
- 记录所有用户与系统交互日志（可导出），便于 review。

### 非目标（本迭代不做）
- 不做真实支付闭环（仅发口令/卡密文本）。
- 不做 Web 管理后台（仅 Telegram 管理命令）。
- 不做多机分布式队列（先内存队列，接口预留可替换）。

---

## Deliverables（交付物）
- 可运行的 Telegram Bot 服务（DEMAND 中说明启动方式）。
- `config/` 下 JSON 配置：`activity.json`、`levels.json`、`rewards.json`（含 5 关题目与示例奖品池）。
- SQLite 数据库初始化/迁移脚本（自动建表）。
- 单元测试 + 集成测试（mock LLM），并提供一键运行命令。
- 管理员命令：toggle、reload_config、stats、ban/unban、export_logs。

---

## Tasks（按 Ralph Loop：逐项实现 → 自测 → 修正）

### 0. 项目初始化与依赖
- [ ] 创建项目结构（src/ 分层：domain/ports/adapters/workers/tests）
- [ ] 选择并固定依赖（建议：Python 3.11+；telegram 框架任选其一；pytest）
- [ ] 增加基础运行脚本（`python -m src.app.main`）与测试脚本（`pytest`）
- [ ] 建立最小化 DI 容器/工厂（container.py），确保各模块按接口注入

**自测**：启动脚本能跑通（即使暂时只打印“启动成功”）。

---

### 1. JSON 配置模块（IContentProvider）
- [ ] 定义配置数据结构（ActivityConfig/LevelConfig/RewardPoolConfig/RewardItem）
- [ ] 实现 `JsonContentProvider`：加载与校验 activity/levels/rewards JSON
- [ ] 加入 reload 能力（文件变更后 reload，或管理员命令触发 reload）
- [ ] 加入基础校验：
  - [ ] level_id 连续、reward_pool_id 存在
  - [ ] RewardItem 的 `max_claims_per_item` 合法（JD_ECARD 必须为 1）
  - [ ] 每关 limits 字段完整（max_input_chars/max_turns/cooldown/max_output_tokens）

**自测**：提供一套 config 文件，启动时成功加载；故意写错字段时能报明确错误。

---

### 2. SQLite 存储模块（IStorage）+ 迁移
- [ ] 设计 SQLite 表：users/sessions/attempts/level_progress/reward_items/reward_claims/log_events
- [ ] 实现 migrations：启动自动建表（幂等）
- [ ] SQLite 连接设置：
  - [ ] `PRAGMA journal_mode=WAL`
  - [ ] `PRAGMA busy_timeout`
- [ ] 实现 IStorage 基础方法：
  - [ ] get_or_create_user
  - [ ] get_session / upsert_session
  - [ ] mark_level_passed / is_level_passed / get_current_level
  - [ ] record_attempt
  - [ ] append_log_event（USER_IN / SYSTEM_OUT / GRADE / REWARD_CLAIM）
- [ ] 实现 `claim_reward(pool_id, user_id, level_id)` 原子方法：
  - [ ] 防重复领取（unique(user_id, level_id)）
  - [ ] 遵守 reward_item 的 max_claims_per_item
  - [ ] 并发下不超发、不重复发同一张京东卡

**自测**：
- 单线程：领取一次成功，第二次 ALREADY_CLAIMED。
- 并发测试：多线程/多 worker 抢京东卡，确保每张只发一次，库存不为负。

---

### 3. 消息渠道模块（IChannelAdapter）— Telegram Adapter（接收/发送解耦）
- [ ] 实现 Telegram adapter：接收消息、发送文本、回复消息
- [ ] 实现 command handlers：/start /help /status /rules /admin*
- [ ] 抽象成统一 Message 对象（user_id/chat_id/message_id/text/ts）
- [ ] 所有发送消息必须走 `IChannelAdapter.send_text`，并写 SYSTEM_OUT 日志

**自测**：用真实 Telegram 测试：
- /start 输出正常
- 任意文本能被接收并进入后续流程（即便暂时只返回“已收到”）

---

### 4. 内存队列与 Worker 并发（IQueue + WorkerPool）
- [ ] 实现 `MemoryQueue`：enqueue/dequeue/qsize
- [ ] 实现 worker 消费循环（并发数来自 activity.json）
- [ ] 入队后立即反馈排队信息（qsize 估算即可）
- [ ] 实现“同用户 inflight 锁”：
  - [ ] INFLIGHT 时拒绝再次提交
  - [ ] 将 inflight 状态写入 session，避免服务重启后失控

**自测**：
- 并发发消息时队列 qsize 增长，worker 逐步消费
- 同一用户连续发两条，第二条应被拒绝提示“处理中”

---

### 5. LLM 调用模块（ILLMClient）+ Mock
- [ ] 定义 ILLMClient：
  - [ ] generate(system_prompt, user_prompt, max_output_tokens)
  - [ ] judge(judge_prompt, max_output_tokens)
- [ ] 实现 OpenAI-compatible client（或占位实现，确保可替换）
- [ ] 实现 `MockLLMClient`（用于测试）：
  - [ ] 可按用例返回指定输出（含“我不能说xxxx”反例）

**自测**：
- generate/judge 都能在 mock 下稳定返回
- max_output_tokens 传参路径完整

---

### 6. 判题模块（关键词 + LLM Judge 双判）
- [ ] 实现 KeywordGrader（exact/包含/可选regex）
- [ ] 实现 LLMJudge（输出 JSON verdict PASS/FAIL）
- [ ] 实现 CompositeGrader：最终 PASS = keyword PASS 且 judge PASS
- [ ] Judge prompt 标准化（包含 level_intro/target/user_prompt/llm_output）
- [ ] Judge 超时/失败策略（需配置）：
  - [ ] 默认：判 FAIL 且不计 turn（或计 turn）——在 activity.json 明确一个默认策略

**自测**（用 mock）：
- 正常包含关键词且非拒绝 => PASS
- “我不能说 {target}” => keyword PASS 但 judge FAIL => 总 FAIL

---

### 7. GameEngine（核心状态机：顺序闯关、轮次、冷却、发奖）
- [ ] 设计 SessionState：READY/INFLIGHT/COOLDOWN/PASSED/FAILED_OUT
- [ ] 实现顺序解锁：必须通关 N 才能挑战 N+1
- [ ] 实现每关限制：
  - [ ] max_input_chars（入队前拦截）
  - [ ] max_turns（达上限 FAILED_OUT）
  - [ ] cooldown_seconds_after_fail（未到拒绝）
- [ ] 成功通关：
  - [ ] 写 level_progress
  - [ ] claim_reward
  - [ ] 发送奖励模板消息（模板变量至少支持 {reward_code} {level_name}）
- [ ] 失败：
  - [ ] turn+1
  - [ ] 写 attempts/logs
  - [ ] 返回剩余轮次与冷却提示

**自测**：
- 不能跳关
- 通关后不能重复参加
- 失败冷却生效（回复后 xx 秒内拒绝）

---

### 8. 奖品发送策略（支付宝共享口令 / 京东卡密一次性）
- [ ] rewards.json 支持：
  - [ ] ALIPAY_CODE：max_claims_per_item 可大于1或无限
  - [ ] JD_ECARD：强制 max_claims_per_item=1
- [ ] send_message_template 可配置，渲染变量：
  - [ ] {reward_code}（必选）
  - [ ] {level_id} {level_name} {username}（可选）
- [ ] 领奖次数限制逻辑覆盖：
  - [ ] 同关卡同用户仅一次（强制）
  - [ ] 同 item 领取次数不超过 max_claims_per_item

**自测**：
- 多用户领取同一个支付宝口令可成功（直到上限）
- 京东卡密同一条不会发给两个人

---

### 9. 全量交互日志（可导出 review）
- [ ] 定义 log_events 表与统一字段（trace_id、event_type、content、level_id、turn）
- [ ] 在关键节点写日志：
  - [ ] USER_IN（用户输入）
  - [ ] SYSTEM_OUT（系统发出文本）
  - [ ] LLM_CALL（可选：耗时/模型）
  - [ ] GRADE（keyword/judge verdict + reason）
  - [ ] REWARD_CLAIM（pool_id/item_id/结果）
- [ ] `/admin export_logs YYYY-MM-DD` 导出 JSONL

**自测**：
- 任意一次答题产生完整链路日志
- 导出文件可读、字段齐全、奖品码默认不明文（可配置是否脱敏）

---

### 10. 管理员命令与运营开关
- [ ] 管理员鉴权（白名单 user_id）
- [ ] `/admin toggle on|off`
- [ ] `/admin reload_config`
- [ ] `/admin stats`（队列长度/worker并发/今日发奖）
- [ ] `/admin ban/unban`
- [ ] `/admin export_logs`

**自测**：非管理员调用应拒绝；管理员调用有效且有日志。

---

### 11. 端到端回归与压测（本地）
- [ ] 使用 MockLLM 完成完整 5 关通关流程回归
- [ ] 并发压测脚本（本地模拟 N 用户请求）：
  - [ ] 验证队列不崩溃、worker 并发受控
  - [ ] 验证 SQLite claim_reward 不超发

**自测**：跑完脚本后：
- E卡领取记录数量 = E卡数量
- 无重复发卡 item_id
- 系统无未捕获异常

---

## Done Definition（完成标准）
- 所有 Tasks 勾选完成，并通过：
  - 单元测试（pytest）全绿
  - 集成测试（mock LLM）全绿
  - 本地并发测试无超发、无重复领取、无崩溃
- 真实 Telegram 手动测试能完成至少 1 关通关 + 发奖
- PRD 对应的 JSON 配置可直接运行（无需手工改代码）

---

## Open Questions（待确认，编码前最后锁定）
- 判题 keyword 的匹配策略：默认 substring 还是 exact？是否区分大小写？
- Judge 超时/失败时策略：判 FAIL 还是“不计轮次重试”？（建议默认不计轮次，提示稍后再试）
- 活动时间窗与时区：是否按北京时间（Asia/Shanghai）？
- 通关后是否允许“练习模式”（可继续对话但不发奖）？
- 奖品码是否允许在日志明文出现（默认脱敏）？
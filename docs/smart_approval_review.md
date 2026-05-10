# 智能审批功能代码审阅（2026-05-09）

## 审阅范围
- `llm_judge.py`
- `llm_integration.py`
- `pending_manager.py`
- `approval_ops.py`

## 总体结论
当前“智能审批”实现方向合理：
1. 通过 LLM 先做风险分级，降低人工审批负担；
2. 使用 pending 队列复用既有审批命令，减少心智成本；
3. 在通知失败、LLM 超时、模型异常时默认拒绝，整体安全基线正确。

## 优点
- **安全默认值正确**：`parse_verdict` 在无法明确判定时回退 `dangerous`，`judge_with_provider` 在超时和异常场景也默认 `dangerous`。
- **与现有审批体系融合度高**：LLM 工具请求通过 `type=llm_tool` 进入统一 pending 流程，支持 `/hapi allow/deny/pending`。
- **审批结果传递链路完整**：`PendingManager.approve_items` 会对 LLM 请求的 `future` 写入结果后再清理条目，避免调用侧悬挂。
- **可见性控制清晰**：管理员/上下文窗口可见 session 双重门控，降低误操作暴露面。

## 发现的问题与风险

### 1) `Future` 创建未绑定当前运行循环（中风险）
`PendingManager.add_llm_tool_request` 使用 `asyncio.Future()` 直接构造 Future。在多 loop 或未来运行时策略更严格时，可能出现 Future 与当前运行上下文不一致的问题。建议改为 `loop = asyncio.get_running_loop(); future = loop.create_future()`。

### 2) `args` 可能为 `None` 时字符串拼接不稳健（低风险）
`LLMIntegration._require_approval` 中 `args_str = ", ".join(f"{k}={v}" for k, v in args.items())` 假设 `args` 始终为 dict。若后续某工具定义参数可空，会抛异常中断审批通知。建议兜底：`args = args or {}`。

### 3) 审批提示的参数展示缺少截断策略（低风险）
审批通知直接展示全部参数，若参数很长（如大段文本）会影响可读性，且可能导致消息过长。建议增加长度限制（例如 300~500 字）并标注已截断。

### 4) 忙时托管“超时自动通过”建议可观测性增强（低风险）
`_require_approval` 在自动托管窗口超时自动批准是合理的业务策略，但建议增加结构化日志字段（window_id/tool_name/index），便于审计和追踪。

## 建议优先级
- P1：修复 Future 创建方式（问题 1）
- P2：参数空值与长文本防御（问题 2、3）
- P3：增强自动托管审计日志（问题 4）

## 回归建议
- 用例 A：普通安全工具调用，LLM 判定 safe，应直接执行无需人工审批；
- 用例 B：危险命令（如 rm -rf），应进入 pending，人工 allow 后继续；
- 用例 C：LLM 超时，默认 dangerous 并进入审批；
- 用例 D：审批消息发送失败，调用应被拒绝并清理 pending；
- 用例 E：审批超时 + 处于托管窗口，应自动放行并落日志。

# 测试手册

部署步骤见 [deployment.md](deployment.md)；架构背景见 [architecture.md](architecture.md)。本篇记录"怎么验证它是对的"以及已经跑过的测试结果。

## 测试数据集

`data/tickets.json` / `data/customers.json` / `data/refund_policy.json` 里预置了 3 个对照工单，覆盖三种典型情况：

| 工单 | 品类 | 预期判断 | 用途 |
|---|---|---|---|
| #4521 | 智能音箱，WiFi 反复断连（本月第 3 次），白金会员 | **该升级**（同一故障 ≥2 次 + 排障未解决，符合换新条件） | 主力演示案例，覆盖多轮工具调用 |
| #4522 | 蓝牙耳机，轻微杂音，普通会员，首次联系 | **不该升级**（轻微问题 + 客户不着急 + 政策要求先排查） | 对照案例，验证模型不会武断升级 |
| #4523 | 智能手表，续航骤降复发（一个月内第 2 次） | **该升级**（30 天内同一故障复发，符合免费更换电池模块/整机换新条件） | 边界情况：单次反馈不算，复发才算 |

冒烟测试：
```bash
python3 scripts/run_demo.py --list-tickets   # 确认数据集能正常加载
```

## 功能验证：跑通完整循环（2026-07-23 实测，Runtime + Strands 方案）

```bash
python3 scripts/run_demo.py --runtime-arn "<ARN>" --ticket-id 4521 --region us-west-2
```

**实测结果**：#4521、#4522、#4523 各跑 1 次，全部一次性走完多轮工具调用并给出**正确方向**的结论：

| 工单 | 判断结果 | 是否正确 | 耗时 |
|---|---|---|---|
| #4521 | 建议换新 + 附赠 3 个月延保（白金会员优先通道） | ✅ | 8.2 秒 |
| #4522 | 不建议退换，先排查固件/配对，问题仍在再走保修 | ✅ | 10.4 秒 |
| #4523 | 建议免费更换电池模块或整机换新 | ✅ | 7.9 秒 |

三次调用里 `get_ticket` → `get_customer_history` → `get_refund_policy` 均按 system prompt 里规定的顺序依次调用，最终都输出了"（一）升级/退款判断 + 依据"和"（二）中文安抚回复草稿"两部分，格式符合预期。

**一次真实的自我纠错**（#4522 测试中观察到）：模型第一次调用 `get_refund_policy` 时传的品类参数有误，工具返回 `{"error": "no policy found for category ..."}`；模型在 `<thinking>` 里识别到"出现了一个错误，可能是因为产品品类名称输入错误"，重新用正确的品类名再调用一次成功——这是 Observe 阶段"判断信息/结果是否有效，不行就自己修正"的一个真实例子，比预先写好的演示脚本更有说服力，现场遇到可以顺势讲。

## 性能基准

用 Nova Pro（`us.amazon.nova-pro-v1:0`）实测全流程耗时（`time python3 scripts/run_demo.py ...`，含 `invoke_agent_runtime` 网络往返）：

| 工单 | 耗时 |
|---|---|
| #4521 | 8.2 秒 |
| #4522 | 10.4 秒 |
| #4523 | 7.9 秒 |

**结论**：全流程只要 8-10 秒，比同场景下 Harness 方案实测的 17-20 秒快了近一半——推测是少了 Harness 的 `inline_function` 客户端往返（每次工具调用都要一次 `invoke_harness` 请求/响应），工具全部跑在容器内部，省掉了这部分网络开销。远低于现场 9-10 分钟的演示预算，彩排时的计时重点应该放在"讲解 CloudWatch 控制台"这一步。

## CloudWatch Trace 可视化验证（2026-07-23 用 Runtime 重新验证过）

**第一次尝试踩了个坑，记录下来避免以后重复踩**：换成 Runtime 后最初跑的几次测试，`aws/spans` 和账号级 Transaction Search 面板里都查不到任何 trace（"All traces" 显示 0），而 Harness 方案下这是自动生效的。排查后发现原因是——**AgentCore Harness 是托管服务，自带 OTel 埋点上报；AgentCore Runtime 只是托管容器，OTel 埋点要自己配**。缺了两样东西：

1. 容器里没装 ADOT（`aws-opentelemetry-distro`）自动埋点包，`agent.py` 里也没有手写任何 OTel 初始化代码。
2. 没有设置 `AGENT_OBSERVABILITY_ENABLED=true` 环境变量——这个开关是 ADOT 决定要不要自动配置 X-Ray/CloudWatch Logs 作为 OTLP 导出目的地的关键条件，`amazon-opentelemetry-distro` 包源码里 `is_agent_observability_enabled()` 直接读这个环境变量，不设就完全不导出。

修复方式（已落到仓库里，不是临时手动改的）：
- `agent/requirements.txt` 加了 `aws-opentelemetry-distro==0.18.0`
- `agent/Dockerfile` 的 `CMD` 从 `python3 agent.py` 改成 `opentelemetry-instrument python3 agent.py`（用 ADOT 的自动埋点包装器启动）
- `runtime/runtime-config.json` 加了 `environmentVariables: {"AGENT_OBSERVABILITY_ENABLED": "true"}`

修完重新构建镜像、重新创建 Runtime、重新跑一次 #4521 后：
- `aws/spans` 日志组在跑完后 90 秒内出现了 17 条 span 事件
- CloudWatch 控制台 `GenAI Observability → Bedrock AgentCore → All traces` 能查到这条 trace（17 spans，8171ms，6221 tokens）
- 点进去的 Trajectory 图完整显示：`POST /invocations → invoke_agent Strands Agents → execute_event_loop_cycle → chat / execute_tool get_ticket / execute_tool get_customer_history / execute_tool get_refund_policy` ——比 Harness 时代的 Trajectory 更细，直接能看到我们自己写的三个工具函数名，现场讲解时可以直接对应到 `agent/agent.py` 里的 `@tool` 代码。
- `docs/screenshots/` 下的四张截图**已经用这次 Runtime 方案重新截过**，不再是 Harness 时代的旧图（旧图里出现的真实账号 ID 已经在新截图里做了遮盖处理，发布仓库前排查过一遍）。

## 已知问题与限制

- **Nova Pro 会在文本里输出字面的 `<thinking>...</thinking>` 标签**：这是 Nova Pro 自己的输出习惯，不是 bug，也不是刻意配置的推理标签。`scripts/run_demo.py` 目前原样把这段文本流式打印出来，现场演示可以顺势讲成"这就是模型自己的思考过程"，但如果要做成更干净的客户可见文案，建议在展示前做一次 `<thinking>...</thinking>` 的过滤/隐藏（本项目未做，保留原始输出以便培训时能拿这个当"模型真实在想什么"的素材）。
- **AgentCore Runtime 的 trace 上报不是自动的**：见上一节，任何基于本项目二次开发、换了 Agent 代码但删掉了 `aws-opentelemetry-distro`/`AGENT_OBSERVABILITY_ENABLED` 配置的人，都会遇到"控制台看不到 trace"且没有任何报错提示的情况（不会抛异常，就是安静地不上报），排查时容易误以为是账号权限或 Transaction Search 配置问题，实际上是这两样东西没配。
- **只测过 Nova Pro，未测过 Kimi 系列模型在 Strands 循环下的表现**：Harness 时代验证过 kimi-k2-thinking 稳定、kimi-k2.5 会概率性提前终止，但那是 Harness 自己的 loop 实现的行为；Strands 的 event loop 实现不同，如果以后要在这套方案里切换模型，必须重新走一遍至少 3 次/工单的稳定性测试，不能直接复用旧结论。
- **每个工单只测过 1-2 次**：三个对照案例目前各只跑了 1-2 次，方向都正确，但没有像 Harness 时代那样做多次重复测试来排除模型概率性行为——如果要用于正式客户培训，建议每个工单至少再跑 2-3 次确认稳定。

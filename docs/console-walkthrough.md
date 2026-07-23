# CloudWatch 控制台逐屏点击脚本

基于 [testing.md](testing.md#cloudwatch-trace-可视化验证2026-07-23-用-runtime-重新验证过) 里 2026-07-23 用 **AgentCore Runtime + Strands** 方案重新跑通、重新截图验证过的结果固化而成（不是 Harness 时代的旧截图，也不是纸面推测）。现场按这份脚本走，不临场探索。截图见 `docs/screenshots/`。

## 前提（培训前必须确认，不能临场发现没做）

1. Transaction Search 已开启：trace 目的地是 `CloudWatchLogs`，不是默认的 `XRay`。
   检查命令：`aws xray get-trace-segment-destination --region <region>`，`Destination` 字段必须是 `CloudWatchLogs`。
2. `aws/spans` 和 `/aws/application-signals/data` 两个日志组已加上允许 `xray.amazonaws.com` 写入的资源策略。
3. Runtime 容器镜像里包含 `aws-opentelemetry-distro`、`CMD` 用 `opentelemetry-instrument` 启动、且 `AGENT_OBSERVABILITY_ENABLED=true` 已设置（见 [deployment.md](deployment.md)）——**少了这一步 trace 不会报错，就是安静地不出现在控制台里**，见 testing.md 里踩过的坑。
4. 已经用当前培训/客户账号至少跑过一次 `run_demo.py --runtime-arn <ARN> --ticket-id 4521`，Trace 数据已经产生（实测跑完后 1-2 分钟内会出现，不要卡点跑）。

## 现场点击步骤

**第 1 步 —— 打开 Trace 列表页**
CloudWatch 控制台 → 左侧导航 `GenAI Observability` → `Bedrock AgentCore` → 顶部标签切到 **"All traces"**。
话术：这张表就是系统内每一次 Agent 调用的完整记录，Trace ID / Spans / Input / Output / 耗时都在，干净直接——先给客户一个"一切都被记录"的印象。
截图：`docs/screenshots/01-all-traces-list.png`

**第 2 步 —— 点开目标 trace**
点开刚跑过的那条（工单 #4521 那次调用，实测 17 个 span，耗时约 8.2 秒，6221 tokens）。
提示：不要在这一页停留讲解字段含义，2 秒内点进详情，避免冷场。

**第 3 步 —— Trajectory 流程图（本次演示最值得展示的一屏）**
进入 trace 详情后，"Trajectory" 面板会看到完整链路：
`POST /invocations → invoke_agent Strands Agents → execute_event_loop_cycle → chat（含 3 轮 us.amazon.nova-pro-v1:0 调用） / execute_tool get_ticket / execute_tool get_customer_history / execute_tool get_refund_policy`
话术：把这个图和刚才 PPT 里的 Perceive→Plan→Act→Observe→Respond 五个阶段对应起来讲——这三个 `execute_tool` 节点名字就是 `agent/agent.py` 里那三个 `@tool` 函数的真实名字，如果现场也开着代码，可以直接指着对应的函数说"这就是 Trajectory 图里这个节点"，比 Harness 时代的黑盒 `execute_event_loop_cycle` 更能讲清楚循环内部在做什么。这一屏是整个 15 分钟里概念落地最直观的地方，多停留讲解。
截图：`docs/screenshots/02-trajectory-diagram.png`

**第 4 步 —— 消息内容面板（跳过前几条工程记录，找有实际内容的那条！）**
右侧面板默认展开的第一条是 OTel 的 resource/scope/span 属性（ARN、SDK 版本、日志组名、`gen_ai.usage.*`、`gen_ai.agent.tools` 等工程指标），和业务内容无关，**不要点开讲，直接往下滚**。
- 继续往下滚，会先看到**用户提问**的可读内容："看一下工单 #4521，判断该不该升级，需不需要退款，并草拟回复。"，连同 system prompt 的完整中文指令一起显示。
  截图：`docs/screenshots/04-message2-content-readable.png`
- 再往下滚，能看到模型最终返回的 `assistant` 消息，包含完整的 `<thinking>...</thinking>` 推理过程和最终答案（升级判断 + 依据 + 回复草稿）。Nova Pro 会把推理过程原样写在 `<thinking>` 标签里，这段本身就是很好的"模型真实在想什么"素材，可以顺势讲。
  截图：`docs/screenshots/03-message-content-thinking.png`
- 提醒：内容是原始 JSON 文本，换行是字面 `\n` 不是真换行，而且滚动定位需要一点手感（鼠标要悬停在右侧 JSON 面板内滚轮才生效，悬停在左侧会滚错面板）——**正式彩排时务必自己实际滚一遍找准位置**，不要临场现找。讲的时候用自己的话复述关键信息，不要逐字念 JSON。
- 如果当次调用里模型有工具调用报错后自我纠正的情况（本项目在 #4522 测试中就真实遇到过一次品类参数传错），可以顺势讲成"循环 Observe 阶段自己发现问题、自己修正"的例子，比预先写好的演示脚本更有说服力——但这是概率性行为，不保证每次彩排都能遇到，别指望现场复现。

**第 5 步 —— 收尾**
回到 PPT 卖点句："这不是关键词分类，是真正综合多个信息源的判断加草拟"，指着刚才的 Trajectory 图强调"这一整套循环现在没有任何工单系统能一键做到"。

## 备用方案

现场网络/权限故障时，用 `docs/screenshots/` 里提前准备好的四张截图（对应上面第 1/3/4 步的画面）按同样顺序讲一遍，话术不变。

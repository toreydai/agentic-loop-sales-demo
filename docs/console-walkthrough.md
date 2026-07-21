# CloudWatch 控制台逐屏点击脚本

基于 [testing.md](testing.md#cloudwatch-trace-可视化验证) 里的实测结论固化而成，并用无头 Chromium 实际登录控制台截图逐屏验证过（不是纸面推测）。现场按这份脚本走，不临场探索。截图见 `docs/screenshots/`。

## 前提（培训前必须确认，不能临场发现没做）

1. Transaction Search 已开启：trace 目的地是 `CloudWatchLogs`，不是默认的 `XRay`。
   检查命令：`aws xray get-trace-segment-destination --region <region>`，`Destination` 字段必须是 `CloudWatchLogs`。
2. `aws/spans` 和 `/aws/application-signals/data` 两个日志组已加上允许 `xray.amazonaws.com` 写入的资源策略。
3. 已经用当前培训/客户账号至少跑过一次 `run_demo.py --ticket-id 4521`，Trace 数据已经产生（Trace 有几分钟的写入延迟，不要卡点跑）。

## 现场点击步骤

**第 1 步 —— 打开 Trace 列表页**
CloudWatch 控制台 → 左侧导航 `GenAI Observability` → `Bedrock AgentCore` → 顶部标签切到 **"All traces"**。
话术：这张表就是系统内每一次 Agent 调用的完整记录，Trace ID / Spans / Input / Output / 耗时都在，干净直接——先给客户一个"一切都被记录"的印象。
截图：`docs/screenshots/01-all-traces-list.png`

**第 2 步 —— 按时间排序，点开目标 trace**
按 Start time 倒序排列，点开刚跑过的那条（对应 `run_demo.py` 那次调用，Spans 数一般是 9-13 之间，明显比只查一次工具的 trace 多）。
提示：不要在这一页停留讲解字段含义，2 秒内点进详情，避免冷场。

**第 3 步 —— Trajectory 流程图（本次演示最值得展示的一屏）**
进入 trace 详情后，左下角 "Trajectory" 面板会看到：
`POST /invocations → invoke_agent Strands Agents → execute_event_loop_cycle → chat`
话术：把这个图和刚才 PPT 里的 Perceive→Plan→Act→Observe→Respond 五个阶段对应起来讲——"这个 execute_event_loop_cycle 就是循环本体，每一次循环都会重新判断信息够不够"。这一屏是整个 15 分钟里概念落地最直观的地方，多停留讲解。
截图：`docs/screenshots/02-trajectory-diagram.png`

**第 4 步 —— 消息内容面板（跳过前几条工程记录，找有实际内容的那条！）**
右侧面板默认展开 **"Message 1: resource, scope…"**——这是 OTel 的 resource/scope/span 属性（ARN、SDK 版本、日志组名、`gen_ai.usage.*` 等工程指标），从 `deployment.environment.name` 一路到 `gen_ai.agent.tools` 全是元数据，和业务内容无关，**不要点开讲，直接往下滚**。
截图：`docs/screenshots/02-trajectory-diagram.png`（右侧可见 Message 1 的元数据样子）
- 继续往下滚，中间可能会经过几条 "Created event" / "Created agent" 之类的生命周期记录（同样是工程日志，跳过）。
- 滚到能看到大段**可读中文 JSON**的地方为止——这里能看到：
  - 模型的 `<thinking>` 推理过程，包括真实跑出来的一个有意思的细节：模型第一次把品类拼成"智能音牌"导致工具报错，自己发现后重新调用改成了"智能音箱"——这个自我纠错的过程本身就是很好的"循环怎么判断信息不够、自己修正"的现场例子。
  - 工具调用（get_ticket / get_customer_history / get_refund_policy）和真实返回结果
  - 最终结论 + 回复草稿
  截图：`docs/screenshots/03-message-content-thinking.png`、`docs/screenshots/04-message2-content-readable.png`
- 提醒：内容是原始 JSON 文本，换行是字面 `\n` 不是真换行，而且滚动定位需要一点手感（鼠标要悬停在右侧 JSON 面板内滚轮才生效，悬停在左侧会滚错面板）——**正式彩排时务必自己实际滚一遍找准位置**，不要临场现找。讲的时候用自己的话复述关键信息，不要逐字念 JSON。

**第 5 步 —— 收尾**
回到 PPT 卖点句："这不是关键词分类，是真正综合多个信息源的判断加草拟"，指着刚才的 Trajectory 图强调"这一整套循环现在没有任何工单系统能一键做到"。

## 备用方案

现场网络/权限故障时，用 `docs/screenshots/` 里提前准备好的四张截图（对应上面第 1/3/4 步的画面）按同样顺序讲一遍，话术不变。

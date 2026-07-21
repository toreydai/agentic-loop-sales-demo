# 测试手册

部署步骤见 [deployment.md](deployment.md)；架构背景见 [architecture.md](architecture.md)。本篇记录"怎么验证它是对的"以及已经跑过的测试结果。

## 测试数据集

`data/tickets.json` / `data/customers.json` / `data/refund_policy.json` 里预置了 3 个对照工单，覆盖三种典型情况：

| 工单 | 品类 | 预期判断 | 用途 |
|---|---|---|---|
| #4521 | 智能音箱，WiFi 反复断连（本月第 3 次），白金会员 | **该升级**（同一故障 ≥2 次 + 排障未解决，符合换新条件） | 主力演示案例，覆盖多轮工具调用 |
| #4522 | 蓝牙耳机，轻微杂音，普通会员，首次联系 | **不该升级**（轻微问题 + 客户不着急 + 政策要求先排查） | 对照案例，验证模型不会武断升级 |
| #4523 | （边界情况，具体见 `data/tickets.json`） | 边界判断 | 补充压力测试，用于模型稳定性验证 |

冒烟测试：
```bash
python3 scripts/run_demo.py --list-tickets   # 确认数据集能正常加载
```

## 功能验证：跑通完整循环

```bash
python3 scripts/run_demo.py --harness-arn "<ARN>" --ticket-id 4521 --region us-west-2
```

**预期输出**（Nova Pro，实测记录）：
- 3-4 轮工具调用：`get_ticket` → `get_customer_history` + `get_refund_policy` →（视情况）补充调用
- 最终输出两部分：（一）升级/退款判断 + 依据，（二）中文安抚回复草稿
- `stopReason=end_turn`，工具调用轮数在 `maxIterations: 8` 以内

同样跑 `--ticket-id 4522` 和 `--ticket-id 4523`，确认三个对照案例判断方向都正确（不该升级的不能被判成该升级，反之亦然）。

## 模型稳定性测试

这是本项目验证工作量最大的部分，记录了完整的测试过程，供以后换模型/复测时参考。

### Nova Pro（生产配置，`harness/harness-config.json`）

2026-07-21 实测：#4521 ×2、#4522 ×1，全部一次性走完多轮工具调用并给出正确结论，**未出现任何异常**。判定为稳定，正式确定为主选模型。

### Kimi K2.5（`moonshotai.kimi-k2.5`，原始 system prompt）

- 工单 #4521（该升级）：表现良好，甚至给出比 Nova Pro 更有条理的判断维度表格。
- 工单 #4522（不该升级）：**连续复现 3 次都提前终止**——模型在文本里说"现在查询客户历史记录和产品退款政策"，但没有真的发起工具调用就直接 `end_turn` 结束，没有给出最终结论。

用同一份代码测 Nova Pro 完全正常，说明这是模型本身的行为问题，不是脚本 bug。

### Kimi K2.5 + 加强版 system prompt（一次不完整的修复尝试）

给 system prompt 加了一条强制规则："决定要查询某个工具时，必须在当前轮直接发起调用，不能只用文字描述"。结果：

- #4522（原本失败的案例）：3/3 通过，问题看似解决。
- #4521（原本一直正常的案例）：**回归失败**，3 次里 2 次只调用了 `get_ticket` 就空白结束（既没有继续调用其他工具，也没有输出任何文本）。

**结论：这是 Kimi K2.5 本身概率性的不稳定，光调整 system prompt 只是把失败案例从一个工单挪到了另一个，没有真正解决问题。** 排查过程中用原始流事件（`invoke_harness` 返回的 stream）确认了这不是脚本解析问题——直接构造同样的多轮消息重放，人工触发同一个 API 调用，问题依然复现，说明是模型侧的概率性行为，不是 `run_demo.py` 的 bug。

### kimi-k2-thinking（`moonshot.kimi-k2-thinking` + 同一份加强版 system prompt，`harness/harness-config-kimi.json`）

换用更强的 thinking 变体后重新测试：#4521、#4522、#4523 各测 2 次，**6/6 全部稳定通过**，包括边界案例 #4523。

**最终结论**：
- `kimi-k2-thinking` + 加强版 system prompt → **验证稳定，可作为备选模型**。
- `kimi-k2.5`（非 thinking 版）→ **不建议用于现场演示**，即使调整 system prompt 也无法根治。

## 性能基准

用 Nova Pro 生产配置实测全流程耗时（`time python3 scripts/run_demo.py ...`）：

| 工单 | 耗时 |
|---|---|
| #4521（第 1 次） | 19.9 秒 |
| #4521（第 2 次） | 19.1 秒 |
| #4522 | 16.6 秒 |

**结论**：模型推理 + 多轮工具调用全流程只要 17-20 秒，远低于现场 9-10 分钟的演示预算。彩排时的计时重点应该放在"讲解 CloudWatch 控制台"这一步，不是模型响应时间——这才是真正的时间风险来源。

## CloudWatch Trace 可视化验证

跑完 `run_demo.py` 后，Trace 数据有几分钟的写入延迟，不要卡点验证。验证方法：

1. 按 [console-walkthrough.md](console-walkthrough.md) 的点击路径，登录 CloudWatch 控制台确认能看到对应 trace。
2. 本项目已用无头 Chromium 实际登录控制台截图验证过完整路径（非纸面推测），截图存档在 `docs/screenshots/`：
   - `01-all-traces-list.png` — All traces 列表页
   - `02-trajectory-diagram.png` — Trajectory 流程图，及 Message 1 的 OTel 元数据样子
   - `03-message-content-thinking.png` — 模型最终结论 + 回复草稿
   - `04-message2-content-readable.png` — 模型 `<thinking>` 推理内容（含一次真实的自我纠错：把"智能音箱"误拼成"智能音牌"导致工具报错，模型自己发现后重新调用改对）
3. 已确认的界面细节：默认展开的第一条 "Message 1" 是纯 OTel 资源/span 元数据，中间可能还夹杂几条 "Created event"/"Created agent" 生命周期日志，都要跳过，滚动到能看到可读中文内容的地方才是真正的业务对话。

## 已知问题与限制

- **Kimi K2.5（非 thinking 版）概率性提前终止**：不建议用于现场演示，见上"模型稳定性测试"。如果以后要切回来，必须先在正式培训账号上重新跑一轮 #4521/#4522/#4523 各至少 3 次确认稳定，不能只信任 prompt 调整。
- **run_demo.py 里两个已修复的流式返回兼容问题**（供换模型时参考是否会重新出现）：
  1. 部分模型会在流里输出长度为 0 的空文本块，但 `InvokeHarness` 要求回传的 `text` 内容最少 1 个字符——已过滤空文本块再回传。
  2. Kimi 通过 Bedrock 返回的 `toolUseId` 格式是 `functions.get_ticket:0`，带有 `InvokeHarness` 正则不允许的 `.`/`:` 字符——已做字符替换清洗（`sanitize_tool_use_id`）。
- **CloudWatch 控制台不能临场自由探索**：消息面板需要一份写死的点击路径（见 console-walkthrough.md），滚动定位需要提前彩排熟悉手感。
- **尚未在真实彩排中实测"讲解环节"耗时**：性能基准只覆盖了模型推理本身（17-20 秒），"点击 CloudWatch 控制台讲解 Trace"这段真人操作的耗时依赖现场熟练度，无法用脚本预先验证，需要真人彩排时用秒表实测。

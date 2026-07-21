# 架构文档

## 定位与差异化

面向对象是**销售**，不是架构师/工程师——这一点决定了整份设计的边界：不讲 API、不讲代码、不做命令行操作，只讲"客户能听懂的价值故事"和"销售自己也能上手演示"的现场脚本。

目标：培训结束后，销售能（1）用一句话讲清楚 Agent Loop 和普通聊天机器人的区别，（2）独立跑一遍现场演示不出错，（3）应对客户 3-5 个高频追问。

时长/场次结构：本次培训是"15 分钟 PPT + 15 分钟 demo"组合场次的后半段。PPT 部分（讲 Agent Loop 概念本身）不在本文档设计范围内；本文档只设计 **15 分钟 demo** 部分，定位是给 PPT 里的理论做一次具体印证，不重复讲一遍概念。

## 核心场景：客服工单升级判定 + 回复起草

选择**客服工单智能升级判定**作为核心场景，原因是打单效果的考量：AWS 账单优化这类"一键报表"AWS 自己的 Trusted Advisor / Cost Explorer 已经做到了，客户很容易反应过来"这不就是换了个壳"，削弱"AI 自己决定下一步"这个核心卖点。客服工单场景目前没有任何现成系统能端到端自动完成，而且几乎所有行业（零售/SaaS/金融/电信）都有客服工单，销售面向任何客户都能用上，不需要临时换场景。

客户提问（培训中反复使用的锚点句）：

> "看一下工单 #4521，判断该不该升级，需不需要退款，并草拟回复"

把这一句话拆成 Agent Loop 的 5 个阶段来讲解：

1. **Perceive（感知）** — Agent 读取工单内容和这位客户的历史记录，识别出这不是关键词分类能解决的，需要综合判断。
2. **Plan（规划）** — 拆解成子任务：查客户等级/历史投诉次数 → 比对退款政策规则 → 判断情绪强度。
3. **Act（行动）** — 调用工单系统查询客户历史（只读），不是凭空编。
4. **Observe（观察）** — 判断信息够不够下结论，不够就再查一次相关商品的退换货政策。
5. **Respond（响应）** — 循环终止条件满足后，输出"建议升级 + 原因"，并草拟一封安抚回复。

给销售的一句话卖点：**"这不是关键词分类，是真正综合多个信息源的判断加草拟——现在没有任何工单系统能一键做到这一步。"**

## 技术架构：AgentCore Harness + CloudWatch GenAI Observability Trace（零代码）

技术路线用 **AgentCore Harness**——AWS 官方文档里 Harness 的介绍原话就是"managed agent loop: reasoning, tool selection, action execution, response streaming"，比 Action Group 这个说法更贴合"Loop"这个培训主题本身，而且能直接复用 `agentcore-workshop` demo05 已跑通的环境。

具体路线：

- 通过 Harness 控制台的 Quick Create 向导（或声明式的 `create-harness` 配置）指定模型 + 工具，零代码、不写编排逻辑。
- 工具接入用内置 `inline_function` 连一套**模拟的工单系统数据**（工单内容、客户历史、退款政策规则），只读查询。
- Trace 可视化走 **CloudWatch GenAI Observability** 的 Bedrock AgentCore Observability（All traces / Trajectory / Messages 面板），现场点开真实的多步调用记录逐步指给客户看，全程点鼠标、不敲命令。

### 架构图

```mermaid
%%{init: {'flowchart': {'curve': 'stepAfter', 'nodeSpacing': 60, 'rankSpacing': 80}}}%%
graph TD
    Sales(["销售 / 客户<br/>现场提问"])
    Sales -->|"看一下工单 #4521，判断该不该升级…"| Harness

    subgraph AWS["AWS 账号（us-west-2）"]
        Harness["AgentCore Harness<br/>Nova Pro（主选）· kimi-k2-thinking（备选）"]
        Client["run_demo.py<br/>inline_function 客户端"]
        Data[("模拟数据<br/>tickets · customers · refund_policy")]
        Observe{"信息够了吗？<br/>Observe"}
        CW["CloudWatch GenAI Observability<br/>Transaction Search"]

        Harness -->|toolUse| Client
        Client -->|查询| Data
        Data -->|结果| Client
        Client -->|toolResult| Observe
        Observe -.->|不够，继续查| Harness
        Harness -->|OTel trace| CW
    end

    Observe ==>|够了| Result(["最终输出<br/>结论 + 回复草稿"])
    CW -->|"All traces / Trajectory / Messages<br/>现场点鼠标讲解"| Result

    classDef actor fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#1f2937,font-weight:bold;
    classDef compute fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1f2937;
    classDef datastore fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#1f2937;
    classDef observability fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#1f2937;
    classDef result fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#1f2937,font-weight:bold;
    classDef decision fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#1f2937;

    class Sales actor;
    class Harness,Client compute;
    class Data datastore;
    class CW observability;
    class Result result;
    class Observe decision;
```

组件构成（具体部署步骤见 [deployment.md](deployment.md)）：

| 组件 | 文件 | 作用 |
|---|---|---|
| 模拟数据集 | `data/tickets.json`、`data/customers.json`、`data/refund_policy.json` | 3 个工单覆盖"明显该升级"（#4521）、"明显不该升级"（#4522）、"边界情况"（#4523）三种对照 |
| Harness 执行角色 | `infra/harness-role.yaml` | CloudFormation 模板（声明式），只包含 inline_function 场景所需的最小权限，不含 Gateway/Browser/Code Interpreter 权限 |
| Harness 生产配置 | `harness/harness-config.json` | Nova Pro 主选模型的 `create-harness` 声明式输入 |
| Harness 备选配置 | `harness/harness-config-kimi.json` | kimi-k2-thinking 备选模型配置，system prompt 加了防止提前终止的强制规则 |
| 调用脚本 | `scripts/run_demo.py` | 因为三个工具是 `inline_function` 类型，按 AgentCore 的设计，工具调用在**客户端**执行、不在 Harness 托管环境里跑——这个脚本就是那个"客户端"，负责在模型发起调用时查本地模拟数据、把结果传回去，循环本身仍由 Harness 控制。这不是绕开声明式路线的手写编排，而是 `inline_function` 这个工具类型本身要求的用法 |
| 控制台点击脚本 | `docs/console-walkthrough.md` | 现场演示时 CloudWatch 控制台的逐屏点击路径 |

## 模型选型（结论）

原计划用 Claude，但 Bedrock 调用被账号级地域限制挡住（"Access to Anthropic models is not allowed from unsupported countries"，与 API 请求指定的区域无关）。这个限制在正式培训要用的账号上反复复测确认持续存在，**Claude 在这次培训里确定不可用**。

- **Amazon Nova Pro**（`us.amazon.nova-pro-v1:0`）—— **确定为正式培训主选模型**。稳定、实测全流程仅需 17-20 秒，远低于现场时间预算。
- **kimi-k2-thinking**（`moonshot.kimi-k2-thinking`，配合 `harness/harness-config-kimi.json` 里加强版 system prompt）—— **验证稳定的备选模型**。
- `kimi-k2.5`（非 thinking 版）—— **不建议用于现场演示**，即使调整 system prompt 也只是把"提前终止"的不稳定现象从一个工单案例挪到另一个，无法根治。

详细测试过程、复现次数、失败模式见 [testing.md](testing.md#模型稳定性测试)。

## 演示流程设计（15 分钟，衔接前面 15 分钟 PPT）

1. **开场对比**（2 分钟）——直接引用 PPT 里刚讲过的"单轮问答 vs Agent Loop"概念，一句话带到"现在看真实的跑一遍"，不重复讲理论。
   **衔接台词（同一人主讲，PPT 和 demo 无缝衔接）**："刚才讲的是 Agent Loop 理论上该怎么工作——Perceive、Plan、Act、Observe、Respond 这五步。光讲概念比较抽象，现在不讲了，直接拿一个真实工单跑一遍给大家看，边跑边指给你们看这五步具体发生在哪。"
2. **现场演示**（9-10 分钟）——真实跑一遍工单场景，在 Trace 面板里逐步讲解 Perceive→Plan→Act→Observe→Respond 5 个阶段（具体点击路径见 [console-walkthrough.md](console-walkthrough.md)）；"循环怎么知道该停"就在讲 Observe 那一步时顺带说清楚，不单独留时间段。
3. **收尾 + 卖点重申**（2-3 分钟）——重复那句话术卖点，预告"常见问题详见发给你们的 battle card"，留 1-2 个问题的现场问答缓冲。

时间风险评估：模型推理本身只要 17-20 秒（见 [testing.md](testing.md#性能基准)），9-10 分钟预算里的瓶颈和不确定性全部来自"讲解 CloudWatch 控制台"这一步，不是模型响应慢。

风险控制：备好一份录屏/截图作为 backup（见 `docs/screenshots/`），防止现场网络或权限问题导致 live demo 失败。

## 常见客户问题与话术要点（battle card，演示结束后作为书面材料发给销售）

- **"这和普通 chatbot 有什么区别？"** → 普通 chatbot 一问一答、答案基于训练时的记忆；Agent Loop 会自己规划步骤、调用真实系统查最新数据、判断信息够不够，不够会自己继续查，最后才给结论。
- **"这不就是关键词分类/工单路由规则引擎吗？"** → 规则引擎只能匹配预先想到的固定条件；这里是模型综合客户等级、历史投诉、退款政策、情绪强度等多个维度做判断，遇到规则引擎没覆盖的边界情况也能给出有依据的结论，而不是命中不了规则就转人工。
- **"客户隐私资料会不会被乱用/泄露给模型训练？"** → 强调 Harness 只做只读查询、工具访问范围显式配置（allowedTools），且模型侧不会把调用数据用于训练；如果客户对数据驻留/合规有更高要求，还可以走 VPC 私有网络模式。这是客服场景比账单场景更容易被追问的点，必须准备好。
- **"这个只能用来做工单分诊吗？"** → 强调这是通用的 Agent Loop 模式，换一套工具（Gateway/MCP 对接 CRM、运维系统、供应链系统等）就能迁移到任何"多步骤查证 + 决策"的业务场景，工单只是今天挑的一个好懂的例子。
- **"要开发多久才能给我们客户定制一个？"** → 如实说明：核心壁垒不在"循环"本身（AgentCore Harness 已经托管），而在于给每个客户接入他们自己的业务系统（Gateway/MCP 的对接工作），这部分需要具体评估工作量，避免现场夸口。

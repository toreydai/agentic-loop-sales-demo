# 部署文档

架构背景见 [architecture.md](architecture.md)。本篇只讲"怎么把它跑起来"。

## 环境要求

- AWS CLI 已配置好凭证，且有权限调用 `cloudformation`、`bedrock-agentcore-control`、`bedrock-agentcore`、`bedrock-runtime`、`xray`、`logs`、`iam`。
- Python 3.9+，`boto3` **必须是较新版本**（默认系统自带的 boto3 1.35 不认识 `bedrock-agentcore` 服务，会报 `UnknownServiceError: Unknown service: 'bedrock-agentcore'`）：
  ```bash
  python3 -m pip install --user -U boto3 botocore
  ```
- 区域固定用 `us-west-2`（本项目所有命令、跨区域推理配置文件都是按这个区域写的）。

## 前置条件：账号级一次性设置

以下两项是账号级配置，不属于本项目要反复部署/清理的资源，**正式培训账号需要提前手动确认/开启一次**，不要等彩排当天才发现漏掉。

### 1. 确认 Claude 是否可用（如果打算用 Claude）

```bash
aws bedrock-runtime invoke-model \
  --model-id us.anthropic.claude-sonnet-4-20250514-v1:0 \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  --region us-west-2 --cli-binary-format raw-in-base64-out /tmp/out.json
```
如果报 `Access to Anthropic models is not allowed from unsupported countries`，说明当前账号有地域限制，Claude 不可用（这是账号级限制，与 `--region` 参数无关）。本项目验证过的两个账号都有此限制，直接用 Nova Pro 或 kimi-k2-thinking，不必纠结解决这个限制。

### 2. 开启 Transaction Search（现场要看 Trace 面板必须做）

Bedrock AgentCore Observability 走 CloudWatch **Transaction Search**，默认 trace 目的地是 `XRay`，必须切到 `CloudWatchLogs`：

```bash
# 检查当前状态
aws xray get-trace-segment-destination --region us-west-2
# 期望输出: {"Destination": "CloudWatchLogs", "Status": "ACTIVE"}
```

如果不是 `CloudWatchLogs`/`ACTIVE`，执行以下步骤：

```bash
# 1. 给两个日志组加资源策略，允许 xray.amazonaws.com 写入
cat > /tmp/xray-logs-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TransactionSearchXRayAccess",
      "Effect": "Allow",
      "Principal": { "Service": "xray.amazonaws.com" },
      "Action": "logs:PutLogEvents",
      "Resource": [
        "arn:aws:logs:us-west-2:<ACCOUNT_ID>:log-group:aws/spans:*",
        "arn:aws:logs:us-west-2:<ACCOUNT_ID>:log-group:/aws/application-signals/data:*"
      ],
      "Condition": {
        "ArnLike": { "aws:SourceArn": "arn:aws:xray:us-west-2:<ACCOUNT_ID>:*" },
        "StringEquals": { "aws:SourceAccount": "<ACCOUNT_ID>" }
      }
    }
  ]
}
EOF
aws logs put-resource-policy \
  --policy-name "TransactionSearchXRayAccess" \
  --policy-document "file:///tmp/xray-logs-policy.json" \
  --region us-west-2

# 2. 切换 trace 目的地
aws xray update-trace-segment-destination --destination CloudWatchLogs --region us-west-2

# 3. 确认（切换后状态可能短暂是 PENDING，等 1-2 分钟再查一次）
aws xray get-trace-segment-destination --region us-west-2
```

不做这一步，正式演示时会直接看到 `AccessDeniedException` 或者压根没有 trace 数据。

## 部署步骤

### Step 1：部署 Harness 执行角色

```bash
cd agentic-loop-sales-demo
aws cloudformation deploy \
  --template-file infra/harness-role.yaml \
  --stack-name agentic-loop-sales-demo-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2

ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name agentic-loop-sales-demo-role --region us-west-2 \
  --query "Stacks[0].Outputs[0].OutputValue" --output text)
```

`infra/harness-role.yaml` 只包含 `inline_function` 场景所需的最小权限（Bedrock 调用、日志、X-Ray、AgentCore workload identity/managed memory），不含 Gateway/Browser/Code Interpreter 权限。

### Step 2：创建 Harness

**主选（Nova Pro）：**
```bash
aws bedrock-agentcore-control create-harness \
  --harness-name SalesLoopTicketDemo \
  --execution-role-arn "$ROLE_ARN" \
  --cli-input-json "file://harness/harness-config.json" \
  --region us-west-2
```

**备选（kimi-k2-thinking）：**
```bash
aws bedrock-agentcore-control create-harness \
  --harness-name SalesLoopTicketDemoKimi \
  --execution-role-arn "$ROLE_ARN" \
  --cli-input-json "file://harness/harness-config-kimi.json" \
  --region us-west-2
```

两份配置的区别只有 `model.bedrockModelConfig.modelId` 和 kimi 版加强的 system prompt（见 [architecture.md](architecture.md#模型选型结论) 里的原因）。

### Step 3：等待 Harness 就绪

```bash
HARNESS_ID="<上一步返回的 harnessId>"
for i in $(seq 1 24); do
  STATUS=$(aws bedrock-agentcore-control get-harness --harness-id "$HARNESS_ID" --region us-west-2 --query "harness.status" --output text)
  echo "check $i: $STATUS"
  [ "$STATUS" = "READY" ] && break
  sleep 10
done
```
实测创建耗时约 2-3 分钟（14-15 次 × 10 秒轮询）。

### Step 4：跑一次验证

```bash
python3 scripts/run_demo.py --harness-arn "<create-harness 返回的 arn>" --ticket-id 4521 --region us-west-2
```
预期看到完整的多轮工具调用日志和最终结论+回复草稿。测试用例详见 [testing.md](testing.md)。

## 配置文件说明

| 文件 | 关键字段 | 说明 |
|---|---|---|
| `infra/harness-role.yaml` | `HarnessAgentName` 参数 | 必须和 `create-harness` 用的 `--harness-name` 一致，用于限定 workload-identity 资源范围 |
| `harness/harness-config.json` | `model.bedrockModelConfig.modelId` | 生产用 `us.amazon.nova-pro-v1:0`（必须用跨区域推理配置文件，直接用 `amazon.nova-pro-v1:0` 会报 `on-demand throughput isn't supported`） |
| `harness/harness-config-kimi.json` | `systemPrompt` 里的"严格规则"段落 | 防止 Kimi 系列模型"说了要继续查却不发起调用就 end_turn"的加强约束，仅这份配置需要，Nova Pro 不需要 |
| 两份配置共有 | `maxIterations: 8` / `timeoutSeconds: 120` | 循环轮数和超时上限，3 个测试工单实测最多用到 4 轮，留了余量 |

`harnessName`/`executionRoleArn` 通过 CLI 参数单独传入，不写死在 JSON 文件里，方便同一份配置在不同 Harness 名称下复用。

## 清理

测试/彩排结束后清理，避免账号里留残留资源：

```bash
aws bedrock-agentcore-control delete-harness --harness-id "$HARNESS_ID" --region us-west-2

aws cloudformation delete-stack --stack-name agentic-loop-sales-demo-role --region us-west-2
aws cloudformation wait stack-delete-complete --stack-name agentic-loop-sales-demo-role --region us-west-2
```

**注意**：Transaction Search 的账号级设置（trace 目的地 + 日志组资源策略）**不要清理**——这是正式培训要用的持久配置，只有 Harness 本身和它的 CloudFormation 栈是"一次性测试资源"。

## 常见部署报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `Access to Anthropic models is not allowed from unsupported countries` | 账号级地域限制，与 `--region` 参数无关 | 换 Nova Pro 或 kimi-k2-thinking，不必尝试解决这个限制 |
| `on-demand throughput isn't supported`（用 `amazon.nova-pro-v1:0` 时） | Nova Pro 不支持直接按需调用 | 改用跨区域推理配置文件 `us.amazon.nova-pro-v1:0` |
| `UnknownServiceError: Unknown service: 'bedrock-agentcore'` | 本地 boto3 版本太旧 | `pip install --user -U boto3 botocore` |
| `XRay does not have permission to call PutLogEvents...` | Transaction Search 的日志组资源策略没配 | 按上面"前置条件"第 2 步先加资源策略，再切换 trace 目的地 |
| 现场看不到 trace / `AccessDeniedException` | Transaction Search 没开启或还在 `PENDING` | 提前一天确认状态是 `CloudWatchLogs`/`ACTIVE`，不要卡点做 |

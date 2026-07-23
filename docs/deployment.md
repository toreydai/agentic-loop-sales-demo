# 部署文档

架构背景见 [architecture.md](architecture.md)。本篇只讲"怎么把它跑起来"。

## 环境要求

- AWS CLI 已配置好凭证，且有权限调用 `cloudformation`、`bedrock-agentcore-control`、`bedrock-agentcore`、`bedrock-runtime`、`ecr`、`xray`、`logs`、`iam`。
- Docker（支持 `buildx`，用于构建 ARM64 镜像——AgentCore Runtime 只接受 ARM64 容器）。
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
如果报 `Access to Anthropic models is not allowed from unsupported countries`，说明当前账号有地域限制，Claude 不可用（这是账号级限制，与 `--region` 参数无关）。本项目验证过的账号有此限制，直接用 Nova Pro，不必纠结解决这个限制。

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

### Step 1：部署执行角色 + ECR 仓库

```bash
cd agentic-loop-sales-demo
aws cloudformation deploy \
  --template-file infra/runtime-role.yaml \
  --stack-name agentic-loop-sales-demo-runtime-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2

ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name agentic-loop-sales-demo-runtime-role --region us-west-2 \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text)
REPO_URI=$(aws cloudformation describe-stacks \
  --stack-name agentic-loop-sales-demo-runtime-role --region us-west-2 \
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" --output text)
```

`infra/runtime-role.yaml` 只包含这个场景所需的最小权限（Bedrock 调用、ECR 拉镜像、日志、X-Ray、AgentCore workload identity），不含 AgentCore Memory 权限（本场景无跨会话记忆需求）。

### Step 2：构建并推送 ARM64 镜像

AgentCore Runtime 只接受 ARM64 容器，构建上下文用仓库根目录（`agent/Dockerfile` 里同时 `COPY` 了 `agent/` 和 `data/` 两个目录）：

```bash
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin "${REPO_URI%%/*}"
docker buildx build --platform linux/arm64 -f agent/Dockerfile -t "$REPO_URI:latest" --load .
docker push "$REPO_URI:latest"
```

### Step 3：创建 Runtime

`runtime/runtime-config.json` 里的 `containerUri` 是占位符 `<ACCOUNT_ID>`（仓库要公开推送到 GitHub，不能把真实账号 ID 写死进配置文件），部署时用当前账号 ID 替换后再传给 `create-agent-runtime`，不直接改仓库里的文件：

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed "s/<ACCOUNT_ID>/$ACCOUNT_ID/" runtime/runtime-config.json > /tmp/runtime-config.json

aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name SalesLoopTicketDemo \
  --role-arn "$ROLE_ARN" \
  --cli-input-json file:///tmp/runtime-config.json \
  --region us-west-2
```

### Step 4：等待 Runtime 就绪

```bash
RUNTIME_ID="<上一步返回的 agentRuntimeId>"
for i in $(seq 1 12); do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region us-west-2 --query "status" --output text)
  echo "check $i: $STATUS"
  [ "$STATUS" = "READY" ] && break
  sleep 10
done
```
实测创建耗时很快，第一次轮询（10 秒内）就已经是 `READY`。

### Step 5：跑一次验证

```bash
python3 scripts/run_demo.py --runtime-arn "<create-agent-runtime 返回的 agentRuntimeArn>" --ticket-id 4521 --region us-west-2
```
预期看到实时流式打印的推理过程和最终结论+回复草稿。测试用例详见 [testing.md](testing.md)。

## 配置文件说明

| 文件 | 关键字段 | 说明 |
|---|---|---|
| `infra/runtime-role.yaml` | `RuntimeName` 参数 | 必须和 `create-agent-runtime` 用的 `--agent-runtime-name` 前缀一致，用于限定 workload-identity 资源范围 |
| `agent/agent.py` | `model_id` | 生产用 `us.amazon.nova-pro-v1:0`（必须用跨区域推理配置文件，直接用 `amazon.nova-pro-v1:0` 会报 `on-demand throughput isn't supported`） |
| `runtime/runtime-config.json` | `agentRuntimeArtifact.containerConfiguration.containerUri` | 指向 ECR 里的镜像地址，换账号部署时必须替换成自己的仓库地址 |
| `runtime/runtime-config.json` | `environmentVariables.AGENT_OBSERVABILITY_ENABLED` | **必须是 `"true"`**，否则容器里的 ADOT 埋点不会导出任何 trace 到 CloudWatch——不会报错，就是控制台查不到数据，排查起来容易误判成账号权限问题（见 [testing.md](testing.md#cloudwatch-trace-可视化验证2026-07-23-用-runtime-重新验证过) 里踩过的坑） |
| `agent/Dockerfile` | `CMD` | 必须用 `opentelemetry-instrument python3 agent.py`，不能是裸的 `python3 agent.py`——前者才会加载 `aws-opentelemetry-distro` 的自动埋点 |

`agentRuntimeName`/`roleArn` 通过 CLI 参数单独传入，不写死在 `runtime-config.json` 里，方便同一份镜像在不同 Runtime 名称下复用。

## 清理

测试/彩排结束后清理，避免账号里留残留资源：

```bash
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region us-west-2
```

`delete-agent-runtime` 是主要的清理动作——Runtime 本身是按调用计费的托管资源。`infra/runtime-role.yaml` 部署出来的 IAM 角色和 ECR 仓库（里面存着已构建好的镜像）建议保留：ECR 存储成本可以忽略不计（一个几十 MB 的小镜像每月几分钱），保留下来下次重新演示只需要一条 `create-agent-runtime` 命令，不用重新走一遍构建+推送。如果确定长期不再使用，再执行：

```bash
aws cloudformation delete-stack --stack-name agentic-loop-sales-demo-runtime-role --region us-west-2
aws cloudformation wait stack-delete-complete --stack-name agentic-loop-sales-demo-runtime-role --region us-west-2
```
（CloudFormation 会自动先清空 ECR 仓库里的镜像再删除仓库。）

**注意**：Transaction Search 的账号级设置（trace 目的地 + 日志组资源策略）**不要清理**——这是正式培训要用的持久配置，只有 Runtime 本身和它的 CloudFormation 栈是"一次性测试资源"。

## 常见部署报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `Access to Anthropic models is not allowed from unsupported countries` | 账号级地域限制，与 `--region` 参数无关 | 换 Nova Pro，不必尝试解决这个限制 |
| `on-demand throughput isn't supported`（用 `amazon.nova-pro-v1:0` 时） | Nova Pro 不支持直接按需调用 | 改用跨区域推理配置文件 `us.amazon.nova-pro-v1:0` |
| `UnknownServiceError: Unknown service: 'bedrock-agentcore'` | 本地 boto3 版本太旧 | `pip install --user -U boto3 botocore` |
| `exec format error` / Runtime 启动失败 | 镜像不是 ARM64 架构 | 构建时必须加 `--platform linux/arm64` |
| `XRay does not have permission to call PutLogEvents...` | Transaction Search 的日志组资源策略没配 | 按上面"前置条件"第 2 步先加资源策略，再切换 trace 目的地 |
| 现场看不到 trace / `AccessDeniedException` | Transaction Search 没开启或还在 `PENDING` | 提前一天确认状态是 `CloudWatchLogs`/`ACTIVE`，不要卡点做 |
| 现场看不到 trace，但也没有任何报错 | `AGENT_OBSERVABILITY_ENABLED` 没设置，或镜像里没装 `aws-opentelemetry-distro` / `CMD` 没用 `opentelemetry-instrument` 启动 | 检查 `runtime/runtime-config.json` 的 `environmentVariables` 和 `agent/Dockerfile` 的 `CMD`，缺一个都不会有任何异常提示 |

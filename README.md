# Agentic Loop 销售 Demo

面向销售的 Agent Loop 现场演示：基于 Amazon Bedrock AgentCore Runtime（Strands Agents SDK）+ CloudWatch GenAI Observability，用"客服工单智能升级判定 + 回复起草"场景，用几十行 Agent 代码显式实现 Perceive→Plan→Act→Observe→Respond 完整循环，现场既能跑演示，也能直接指着代码讲循环怎么转的。

---

## 架构概览

```
销售话术锚点句："看一下工单 #4521，判断该不该升级，需不需要退款，并草拟回复"
    │
    ▼
AgentCore Runtime（容器托管，Strands Agent 显式实现 Perceive→Plan→Act→Observe→Respond）
    │  Nova Pro（us.amazon.nova-pro-v1:0）
    ├── get_ticket           → 模拟工单数据（data/tickets.json，容器内本地调用）
    ├── get_customer_history → 模拟客户历史（data/customers.json，容器内本地调用）
    └── get_refund_policy    → 模拟退款政策（data/refund_policy.json，容器内本地调用）
    │
    ▼
CloudWatch GenAI Observability（Transaction Search）
    → All traces 列表 → Trajectory 流程图 → Message 面板（现场点鼠标讲解，不敲命令）
```

详细设计动机、场景选型、与 AgentCore Harness 方案的取舍见 [架构文档](docs/architecture.md)。

---

## 快速开始

```bash
# 1. 部署执行角色 + ECR 仓库
aws cloudformation deploy --template-file infra/runtime-role.yaml \
  --stack-name agentic-loop-sales-demo-runtime-role --capabilities CAPABILITY_NAMED_IAM --region us-west-2

# 2. 构建镜像并推送到 ECR
REPO_URI=$(aws cloudformation describe-stacks --stack-name agentic-loop-sales-demo-runtime-role \
  --region us-west-2 --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" --output text)
docker buildx build --platform linux/arm64 -f agent/Dockerfile -t "$REPO_URI:latest" --load .
docker push "$REPO_URI:latest"

# 3. 创建 Runtime（runtime-config.json 里的 containerUri 是占位符，用当前账号 ID 替换后再传入）
ROLE_ARN=$(aws cloudformation describe-stacks --stack-name agentic-loop-sales-demo-runtime-role \
  --region us-west-2 --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text)
sed "s/<ACCOUNT_ID>/$(aws sts get-caller-identity --query Account --output text)/" runtime/runtime-config.json > /tmp/runtime-config.json
aws bedrock-agentcore-control create-agent-runtime --agent-runtime-name SalesLoopTicketDemo \
  --role-arn "$ROLE_ARN" --cli-input-json file:///tmp/runtime-config.json --region us-west-2

# 4. 跑一次演示（等 Runtime 状态变 READY 后）
python3 scripts/run_demo.py --runtime-arn <上一步返回的 agentRuntimeArn> --ticket-id 4521 --region us-west-2
```

账号前置条件（Transaction Search 开启）、完整部署/清理步骤见 [部署文档](docs/deployment.md)。

---

## 文档

| 文档 | 说明 |
|---|---|
| [架构文档](docs/architecture.md) | 培训定位、核心场景与 Agent Loop 五阶段映射、技术架构、与 Harness 方案的取舍、演示流程与话术 battle card |
| [部署文档](docs/deployment.md) | 环境要求、账号级前置条件、部署步骤、配置文件说明、清理、常见报错 |
| [测试手册](docs/testing.md) | 测试数据集、功能验证、性能基准、trace 可视化验证、已知问题 |
| [控制台点击脚本](docs/console-walkthrough.md) | 现场演示时 CloudWatch 控制台的逐屏点击路径，含截图 |

## 目录结构

```
data/       模拟数据集：工单 / 客户历史 / 退款政策
agent/      Strands Agent 代码 + Dockerfile：三个工具随容器一起跑在 Runtime 里
infra/      Runtime 执行角色 + ECR 仓库的 CloudFormation 模板
runtime/    create-agent-runtime 的声明式配置（容器镜像地址 + 网络/协议配置）
scripts/    run_demo.py：纯客户端调用脚本，只负责发问题、实时打印流式回复
docs/       架构、部署、测试文档 + 控制台截图
```

## 注意事项

- 演示/测试完成后执行 `delete-agent-runtime` 清理测试资源（见部署文档「清理」一节），避免持续产生费用；执行角色和 ECR 仓库栈可以保留，下次重新演示只需一条 `create-agent-runtime` 命令，不用重新构建镜像。
- Transaction Search 的账号级设置（trace 目的地、日志组资源策略）是正式培训要用的持久配置，**不要**跟着测试资源一起清理。

## License

MIT - see the [LICENSE](LICENSE) file for details.

## 免责声明

- 本项目仅供销售培训演示与技术参考，不构成生产部署方案。
- 演示数据（工单、客户历史、退款政策）均为虚构模拟数据，不涉及任何真实客户信息。
- 运行过程中会创建 AWS 资源（AgentCore Runtime、ECR 仓库、IAM 角色）并产生费用，请在演示/测试结束后及时清理，见上文「注意事项」。
- 作者不对因使用本项目产生的任何费用或损失承担责任。
- 本项目与 Amazon Web Services 无官方关联，相关服务的可用性、定价与地域限制以 AWS 官方文档为准。

#!/usr/bin/env python3
"""Sales demo runner: 客服工单升级判定 + 回复起草，跑在 AgentCore Runtime 上。

Runtime 里的 agent.py 用 Strands Agents SDK 显式实现了 Perceive→Plan→Act→
Observe→Respond 循环，三个工具（get_ticket / get_customer_history /
get_refund_policy）也随容器一起跑在 Runtime 托管环境里——和 Harness 的
inline_function（工具在客户端执行）不同，这个脚本现在只是个纯客户端：发一个
问题，实时打印流式返回的文本，循环本身完全在 Runtime 容器里跑完。

用法:
  python3 run_demo.py --runtime-arn <ARN> --ticket-id 4521
  python3 run_demo.py --list-tickets
"""
import argparse
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TICKETS = json.loads((DATA_DIR / "tickets.json").read_text())


def main():
    parser = argparse.ArgumentParser(description="AgentCore Runtime 工单分诊 demo")
    parser.add_argument("--runtime-arn", help="create-agent-runtime 返回的 agentRuntimeArn")
    parser.add_argument("--ticket-id", default="4521", help="要演示的工单编号")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--list-tickets", action="store_true", help="列出可用的模拟工单编号后退出")
    args = parser.parse_args()

    if args.list_tickets:
        for t in TICKETS:
            print(f"#{t['ticket_id']}  [{t['product_category']}]  {t['subject']}")
        return

    if not args.runtime_arn:
        parser.error("--runtime-arn 是必需的（--list-tickets 除外）")

    client = boto3.client("bedrock-agentcore", region_name=args.region)

    question = f"看一下工单 #{args.ticket_id}，判断该不该升级，需不需要退款，并草拟回复。"
    print(f"\n=== 客户提问 ===\n{question}\n")
    print("=== Agent 回复（实时流式） ===")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=args.runtime_arn,
        payload=json.dumps({"prompt": question}, ensure_ascii=False).encode("utf-8"),
    )

    final_result = None
    for line in response["response"].iter_lines():
        if not line or not line.startswith(b"data: "):
            continue
        event = json.loads(line[len(b"data: "):])
        if "data" in event:
            print(event["data"], end="", flush=True)
        elif "result" in event:
            final_result = event["result"]

    print(f"\n\n=== 结束 ===")
    if final_result:
        print(f"（最终结果长度 {len(final_result)} 字符，与流式打印内容一致）")


if __name__ == "__main__":
    main()

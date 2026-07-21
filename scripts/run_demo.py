#!/usr/bin/env python3
"""Sales demo runner: 客服工单升级判定 + 回复起草，跑在 AgentCore Harness 上。

Harness 的三个工具（get_ticket / get_customer_history / get_refund_policy）都是
inline_function 类型——按 AgentCore 的设计，这类工具在客户端（也就是这个脚本）
执行，不在 Harness 的托管环境里跑。所以这个脚本不是在"重新实现 loop"，只是在
扮演"外部业务系统"的角色：模型决定要查什么，脚本负责把对应的本地模拟数据答复
回去，循环由 Harness 自己控制。

用法:
  python3 run_demo.py --harness-arn <ARN> --ticket-id 4521
  python3 run_demo.py --list-tickets
"""
import argparse
import json
import re
import uuid
from pathlib import Path

import boto3


def sanitize_tool_use_id(raw_id):
    """InvokeHarness 要求 toolUseId 匹配 [a-zA-Z0-9_-]+；部分模型（如 Kimi）会返回
    'functions.get_ticket:0' 这种带 '.'/':' 的 ID，这里统一替换成下划线再回传。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw_id)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

TICKETS = json.loads((DATA_DIR / "tickets.json").read_text())
CUSTOMERS = json.loads((DATA_DIR / "customers.json").read_text())
REFUND_POLICY = json.loads((DATA_DIR / "refund_policy.json").read_text())
TICKETS_BY_ID = {t["ticket_id"]: t for t in TICKETS}


def get_ticket(args):
    ticket = TICKETS_BY_ID.get(args.get("ticket_id"))
    return ticket if ticket else {"error": f"ticket {args.get('ticket_id')} not found"}


def get_customer_history(args):
    record = CUSTOMERS.get(args.get("customer_id"))
    return record if record else {"error": f"customer {args.get('customer_id')} not found"}


def get_refund_policy(args):
    policy = REFUND_POLICY.get(args.get("product_category"))
    return {"policy": policy} if policy else {"error": f"no policy found for category {args.get('product_category')}"}


TOOL_HANDLERS = {
    "get_ticket": get_ticket,
    "get_customer_history": get_customer_history,
    "get_refund_policy": get_refund_policy,
}


def run_turn(client, harness_arn, session_id, messages):
    """调用一次 invoke_harness，把文本实时打印到 stdout，返回 (stop_reason, tool_use_blocks, assistant_content)。"""
    response = client.invoke_harness(harnessArn=harness_arn, runtimeSessionId=session_id, messages=messages)

    stop_reason = None
    blocks = {}  # contentBlockIndex -> {"type": "text"|"toolUse", ...}

    for event in response["stream"]:
        if "contentBlockStart" in event:
            idx = event["contentBlockStart"]["contentBlockIndex"]
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                blocks[idx] = {
                    "type": "toolUse",
                    "toolUseId": sanitize_tool_use_id(start["toolUse"]["toolUseId"]),
                    "name": start["toolUse"]["name"],
                    "input": "",
                }
            else:
                blocks[idx] = {"type": "text", "text": ""}
        elif "contentBlockDelta" in event:
            idx = event["contentBlockDelta"]["contentBlockIndex"]
            delta = event["contentBlockDelta"].get("delta", {})
            block = blocks.setdefault(idx, {"type": "text", "text": ""})
            if "text" in delta:
                block["text"] = block.get("text", "") + delta["text"]
                print(delta["text"], end="", flush=True)
            elif "toolUse" in delta:
                block["input"] = block.get("input", "") + delta["toolUse"].get("input", "")
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
        elif "runtimeClientError" in event:
            raise RuntimeError(event["runtimeClientError"]["message"])

    assistant_content = []
    tool_use_blocks = []
    for idx in sorted(blocks):
        b = blocks[idx]
        if b["type"] == "text":
            if b["text"]:  # InvokeHarness 要求 text 内容最少 1 个字符，跳过空文本块（部分模型会输出空白块）
                assistant_content.append({"text": b["text"]})
        else:
            tool_input = json.loads(b["input"]) if b["input"] else {}
            assistant_content.append({"toolUse": {"toolUseId": b["toolUseId"], "name": b["name"], "input": tool_input}})
            tool_use_blocks.append({"toolUseId": b["toolUseId"], "name": b["name"], "input": tool_input})

    return stop_reason, tool_use_blocks, assistant_content


def main():
    parser = argparse.ArgumentParser(description="AgentCore Harness 工单分诊 demo")
    parser.add_argument("--harness-arn", help="create-harness 返回的 harness ARN")
    parser.add_argument("--ticket-id", default="4521", help="要演示的工单编号")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--list-tickets", action="store_true", help="列出可用的模拟工单编号后退出")
    args = parser.parse_args()

    if args.list_tickets:
        for t in TICKETS:
            print(f"#{t['ticket_id']}  [{t['product_category']}]  {t['subject']}")
        return

    if not args.harness_arn:
        parser.error("--harness-arn 是必需的（--list-tickets 除外）")

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    session_id = f"{uuid.uuid4()}-{uuid.uuid4()}"  # InvokeHarness 要求 runtimeSessionId 至少 33 个字符

    question = f"看一下工单 #{args.ticket_id}，判断该不该升级，需不需要退款，并草拟回复。"
    print(f"\n=== 客户提问 ===\n{question}\n")
    print("=== Agent 回复（实时流式） ===")

    messages = [{"role": "user", "content": [{"text": question}]}]
    stop_reason, tool_calls, assistant_content = run_turn(client, args.harness_arn, session_id, messages)

    round_num = 1
    while stop_reason == "tool_use":
        print(f"\n\n--- [第 {round_num} 轮工具调用] ---")
        tool_result_content = []
        for call in tool_calls:
            handler = TOOL_HANDLERS.get(call["name"])
            result = handler(call["input"]) if handler else {"error": f"unknown tool {call['name']}"}
            print(f"调用 {call['name']}({call['input']}) -> {result}")
            tool_result_content.append({
                "toolResult": {
                    "toolUseId": call["toolUseId"],
                    "content": [{"text": json.dumps(result, ensure_ascii=False)}],
                    "status": "error" if "error" in result else "success",
                }
            })
        print("--- [Agent 继续推理] ---\n")
        messages = [
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": tool_result_content},
        ]
        stop_reason, tool_calls, assistant_content = run_turn(client, args.harness_arn, session_id, messages)
        round_num += 1

    print(f"\n\n=== 结束，stopReason={stop_reason}，共 {round_num - 1} 轮工具调用 ===")


if __name__ == "__main__":
    main()

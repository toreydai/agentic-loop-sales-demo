"""客服工单分诊 Agent：跑在 AgentCore Runtime 里，用 Strands Agents SDK 显式实现
Perceive→Plan→Act→Observe→Respond 循环（循环本体由 strands.Agent 的事件循环驱动，
不是自己手写 while 循环）。

三个工具在这里就是普通函数，随容器一起跑在 Runtime 托管环境里——这一点和
AgentCore Harness 的 inline_function（工具在客户端执行）是本质区别。
"""
import json
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

DATA_DIR = Path(__file__).resolve().parent / "data"

TICKETS = {t["ticket_id"]: t for t in json.loads((DATA_DIR / "tickets.json").read_text())}
CUSTOMERS = json.loads((DATA_DIR / "customers.json").read_text())
REFUND_POLICY = json.loads((DATA_DIR / "refund_policy.json").read_text())

SYSTEM_PROMPT = (
    "你是一家消费电子产品公司的客服工单分诊助手。收到工单编号后，你必须："
    "1) 调用 get_ticket 查看工单详情；2) 调用 get_customer_history 查看该客户的会员等级、"
    "历史投诉次数和情绪记录；3) 调用 get_refund_policy 查看对应产品品类的退款/换新政策，"
    "判断当前情况是否满足升级或退款条件；4) 如果前几步信息不足以下结论（例如政策里提到的"
    "“重复次数”门槛需要对照历史投诉次数才能判断），再补充调用相应工具，不要在信息"
    "不全时武断下结论。最终必须输出两部分：（一）是否建议升级/退款，并给出依据；（二）一封"
    "可以直接发给客户的中文安抚回复草稿，语气专业、有同理心，不做超出政策范围的承诺。"
)


@tool
def get_ticket(ticket_id: str) -> dict:
    """根据工单编号查询工单详情，包括客户ID、产品品类、工单正文和创建时间。"""
    return TICKETS.get(ticket_id, {"error": f"ticket {ticket_id} not found"})


@tool
def get_customer_history(customer_id: str) -> dict:
    """根据客户ID查询该客户的会员等级、历史投诉次数、生命周期价值和情绪记录。"""
    return CUSTOMERS.get(customer_id, {"error": f"customer {customer_id} not found"})


@tool
def get_refund_policy(product_category: str) -> dict:
    """根据产品品类查询对应的退款/换新政策文本。"""
    policy = REFUND_POLICY.get(product_category)
    return {"policy": policy} if policy else {"error": f"no policy found for category {product_category}"}


agent = Agent(
    model=BedrockModel(model_id="us.amazon.nova-pro-v1:0", temperature=0.2, max_tokens=2048),
    system_prompt=SYSTEM_PROMPT,
    tools=[get_ticket, get_customer_history, get_refund_policy],
)

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload):
    question = payload.get("prompt", "")
    async for event in agent.stream_async(question):
        if "data" in event:
            yield {"data": event["data"]}
        elif "result" in event:
            yield {"result": str(event["result"])}


if __name__ == "__main__":
    app.run()

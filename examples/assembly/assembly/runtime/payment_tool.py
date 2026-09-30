"""把场景文件中的原始支付流水提供给 Hermes，不在工具里预判业务结论。"""
from __future__ import annotations

import json
from typing import Any

from .. import config

TOOLSET = "assembly_payments"
TOOL_NAME = "query_order_payments"


def query_order_payments(order_id: str) -> dict[str, Any]:
    """每次实际读取 orders.json；未知订单按工具错误返回。"""
    orders = json.loads((config.SCENARIOS_DIR / "orders.json").read_text(encoding="utf-8"))
    if order_id not in orders:
        raise ValueError(f"找不到订单：{order_id}")
    return {"order_id": order_id, **orders[order_id]}


def _handle(arguments: dict[str, Any], **_context: Any) -> str:
    return json.dumps(query_order_payments(arguments["order_id"]), ensure_ascii=False)


def register_payment_tool() -> None:
    """使用上游 ToolRegistry 的 schema + handler 接口注册。"""
    from tools.registry import registry

    existing = registry.get_entry(TOOL_NAME)
    if existing is not None:
        if existing.handler is not _handle:
            raise RuntimeError(f"工具名已被其他实现占用：{TOOL_NAME}")
        return
    registry.register(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema={
            "name": TOOL_NAME,
            "description": "查询订单应付金额、各笔支付流水的真实状态及商户通知信息。",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "订单编号"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
        handler=_handle,
        description="本地订单支付查询",
    )

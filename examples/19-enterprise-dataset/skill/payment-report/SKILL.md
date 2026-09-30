---
name: payment-report
description: 根据任务开始时的只读订单、账务和渠道快照核对支付状态，生成有依据的报告；不处理扣款、结算或需要用户补充信息的任务。
---

# 支付核查

报告的价值在于说清这笔钱在指定时点究竟处于什么状态。渠道受理只能证明请求已经接收，不能当成账务已经入账；同一个订单号出现在不同商户时，必须用商户与订单共同关联。

运行器已经把当题 request、orders、ledger、channel 四张只读快照原样放入本次请求。先核对这四类资料，不再调用工具。任务给出的商户、订单范围、币种和 as_of 是这次核查的边界，只读这些快照，不访问当前线上状态。可以自己安排内部查询顺序，但不要向用户追问或复用过去的报告。

只统计 merchant_id、order_id、currency 都匹配且发生时间不晚于 as_of 的账务记录。仅 posted 的 payment 增加有效付款，posted 的 refund 扣减有效付款。同一 entry_id 重复出现只计一次。渠道 accepted 或 processing 且没有足额付款时，不能直接判定到账。

每个目标订单都要返回。effective_paid_cents 为净有效付款，difference_cents 为 due_cents 减净有效付款。净额超过应付为 overpaid，恰好足额为 paid，正数但不足为 partial；净额为零时，有有效受理记录为 pending，否则 unpaid。金额用整数分，保留负差额。evidence_ids 列出所有被纳入计算的账务 entry_id；净额为零且判 pending 时，改列支持该状态的渠道 receipt_id。不能用别的商户、币种或未来记录作依据。

只输出约定格式的纯 JSON，不写任何分析前后缀，也不使用 Markdown 代码围栏；判断依据写在 JSON 的 explanation 字段中。

JSON 对象结构是 {"orders": [{"merchant_id": "...", "order_id": "...", "effective_paid_cents": 0, "difference_cents": 0, "status": "paid", "evidence_ids": ["..."], "explanation": "说明判断依据"}]}。explanation 可用自己的话说明，不能替代硬字段和来源编号。不要输出历史满意度或采纳值。

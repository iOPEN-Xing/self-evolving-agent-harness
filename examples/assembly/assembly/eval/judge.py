"""订单付款场景的确定性评分器。

把金额、交易身份、状态、结论及处理建议分别核对。评分器只支持本目录
订单数据中定义的付款语义；未知字段保守判为不完整，不靠数字共现放行。
评分不调用模型，因此同一答案始终得到同一判定。
"""
from __future__ import annotations

import copy
import json
import re
import unicodedata
from typing import Any

from ..config import SCENARIOS_DIR
from ..contracts import CaseResult, JudgeSelfCheck

EVALUATOR_VERSION = "payment-structured-judge/1.2.0"

_SUCCESS = r"(?:支付成功|付款成功|交易成功|成功支付|成功付款|成功到账|已经到账|已经入账|已经成功|已到账|已入账|已支付|已付款|到账成功|均成功|都成功|均到账|都到账|已成功|success|成功)"
_PROCESSING = r"(?:processing|处理中|处理之中|待确认|等待确认|尚待确认|在途|尚未最终到账|未最终到账)"
_DUE = r"(?:(?<!剩余)(?<!尚需)(?<!仍需)(?<!还需)(?<!未)应付(?:款)?(?:金额)?|应收(?:金额)?|应缴(?:金额)?|订单(?:总)?金额|订单总额|需付(?:金额)?)"
_REMAINING = r"(?:还差|尚差|差额|剩余(?:应付)?|尚欠|未付(?:金额)?|待付(?:金额)?|未到账(?:金额)?|尚未确认到账金额)"
_PAID = r"(?:已付清|已结清|付清了|结清了|已经付清|已经结清|全部付清|全部结清|已全额支付|全额到账|款项已齐|无欠款|没有欠款|无需再付|可以关单|可关单|已支付完成)"
_NOT_PAID = r"(?:未付清|未结清|没付清|没有付清|没结清|尚未结清|还没结清|未最终到账|尚未到账|尚未最终到账|未最终支付成功|还未付清|是否(?:结清|付清)[:：|]否|不能.{0,5}(?:判|算|视为|标为|标记为).{0,3}(?:已付清|付清|结清|支付成功|到账)|不代表(?:款项)?(?:已经|已|最终)?(?:付清|支付成功|到账))"


def _chinese_number(token: str) -> str:
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"两": 2, "〇": 0})
    total = current = 0
    for char in token:
        if char in digits:
            current = digits[char]
        elif char in "十百千万":
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            if unit == 10000:
                total = (total + current) * unit
            else:
                total += (current or 1) * unit
            current = 0
    return str(total + current)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    text = re.sub(r"[零〇一二三四五六七八九两十百千万]*[十百千万][零〇一二三四五六七八九两十百千万]*", lambda m: _chinese_number(m.group()), text)
    text = text.replace("人民币", "").replace("**", "").replace("`", "")
    text = re.sub(r"(txn-[a-z]+)\s*(?=\d)", r"\1|", text)
    text = re.sub(r"(ord-[a-z]+\d+)\s+(?=\d)", r"\1|", text)
    return re.sub(r"[ \t\u3000]+", "", text)


def _clauses(text: str) -> list[str]:
    return [part for part in re.split(r"[，,。；;！？!?\n]", text) if part]


def _present_facts(text: str) -> str:
    """未来分支不是当前断言；作用域截至句号、分号或换行。"""
    parts = re.split(r"([。；;！？!?\n])", text)
    for i in range(0, len(parts), 2):
        part = parts[i]
        condition = re.search(r"如果|假如|假设|若|一旦|只有当|确认失败后|(?:(?<!等)待|等)(?:交易|款项|支付|渠道|txn-).{0,18}(?:变为|确认|成功|完成|返回)", part)
        if condition:
            tail = part[condition.start():]
            # “如果……，但实际上……”后重新成为当前事实。
            actual = re.search(r"[，,](?:但)?(?:实际上|事实上|目前|现在)", tail)
            parts[i] = part[:condition.start()] + (tail[actual.end():] if actual else "")
    return "".join(parts)


def _negated(clause: str, start: int) -> bool:
    """只看本分句的否定/转述，不让前句的“不”抵消后句错误断言。"""
    prefix = clause[:start]
    # “不成功”中的“不”与“不能判为已付清”都是非肯定断言。
    if re.search(r"(?:不能|不可|不应|不得|不要|禁止|避免|并非|不是|不算|不代表|不等于|尚未|并未|还未|未能|没有|不可以|无法|不支持|不能把|不能将|无需|不必|不需要|不计入|不纳入|未计入|未纳入|不计为|不算作)[^:：|]{0,22}$", prefix):
        return True
    if re.search(r"(?:未|不|没)$", prefix):
        return True
    if re.search(r"(?:商户|客户|用户)(?:称|说|反馈|表示|反映)[^|]{0,25}$", prefix):
        return True
    if re.search(r"(?:错误说法|错误结论|不要回答|不应回答|例如错误)[^|]{0,16}$", prefix):
        return True
    if re.search(r"(?:如果|假如|假设|若|一旦)[^:：|]{0,15}$", prefix):
        return True
    if re.search(r"(?:等待|等候|轮询).{0,30}$", prefix):
        return True
    if re.search(r"(?<!已)确认其从processing(?:转为|变为)$", prefix):
        return True
    if re.search(r"(?:须以|需以|应以).{0,25}$", prefix):
        return True
    return False


def _assertions(text: str, pattern: str) -> list[str]:
    result = []
    for clause in _clauses(text):
        for match in re.finditer(pattern, clause, re.I):
            if not _negated(clause, match.start()):
                result.append(clause)
                break
    return result


def _number(amount: float | int) -> str:
    return rf"(?<![\d.]){float(amount):g}(?:\.0+)?(?![\d.])"


def _money(text: str, amount: float | int, role: str, distance: int = 12) -> bool:
    """金额必须和同一分句中的角色相连，不能用答案任意位置的数字凑齐。"""
    number = _number(amount)
    for clause in _clauses(text):
        for m in re.finditer(role, clause, re.I):
            if _negated(clause, m.start()):
                continue
            before, after = clause[:m.start()], clause[m.end():]
            arithmetic = re.match(r"[^\d]{0,12}(\d+(?:\.\d+)?(?:\+\d+(?:\.\d+)?)+)=(\d+(?:\.\d+)?)", after)
            if arithmetic:
                value = sum(float(term) for term in arithmetic.group(1).split("+"))
                if value == float(arithmetic.group(2)) == float(amount):
                    return True
                continue
            following = re.match(rf"[^\d]{{0,{distance}}}(\d+(?:\.\d+)?)(%?)", after)
            if following:
                if not following.group(2) and float(following.group(1)) == float(amount):
                    return True
                continue
            if re.search(number + rf"[^\d]{{0,{distance}}}$", before):
                return True
    return False


def _has_amount(text: str, amount: float | int) -> bool:
    return any(re.search(_number(amount), clause) and re.search(r"元|块|金额|应付|应收|应缴|支付|付款|交易|到账|processing|处理中|txn-|合计|总计", clause) for clause in _clauses(text))


def _transaction_segments(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    # 支持普通句子与 Markdown 表格，每行/句中交易 id 后的信息归该笔交易。
    for line in re.split(r"[。；;\n]", text):
        matches = list(re.finditer(r"txn-[a-z0-9]+", line))
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            segment = line[match.start():end]
            # 下一分句的总额/结论不能被当成该笔交易金额。
            segment = re.split(r"[，,](?=(?:合计|总计|共计|一共|成功金额|成功到账金额|应付|应收|还差|剩余|因此|所以))", segment)[0]
            result.append((match.group().upper(), segment))
    return result


def _two_success(text: str, order: dict[str, Any], require_each: bool) -> bool:
    success_txns = [t for t in order["transactions"] if t["status"] == "success"]
    if len(success_txns) != 2:
        return False
    segments = _transaction_segments(text)
    individually = all(any(txid == txn["txn_id"] and _assertions(seg, _SUCCESS) and (not require_each or re.search(_number(txn["amount"]), seg)) for txid, seg in segments) for txn in success_txns)
    if individually:
        return True
    for clause in _clauses(text):
        if re.search(r"(?:两笔|2笔|二笔|两次|2次)", clause) and _assertions(clause, _SUCCESS):
            if not require_each:
                return True
            each = success_txns[0]["amount"]
            if re.search(r"(?:各|每笔(?:均|都是|为)?|每次)" + rf"[^\d]{{0,4}}{_number(each)}", clause):
                return True
    # “两笔各450元，均成功”允许同一行相邻分句继承明确主语。
    return bool(require_each and re.search(r"(?:两笔|2笔|二笔)(?:款项|交易|付款)?(?:各|每笔)[^\d]{0,4}" + _number(success_txns[0]["amount"]) + r"[^。；;\n]{0,15}(?:均成功|都成功|均到账|都到账|都已到账)", text))


def _unsupported(text: str, order_id: str, order: dict[str, Any], original: str = "") -> list[str]:
    problems: list[str] = []
    txns = {t["txn_id"]: t for t in order["transactions"]}
    for other in set(re.findall(r"ord-[a-z0-9]+", text)):
        if other.upper() != order_id:
            problems.append(f"答案引用了其他订单 {other.upper()}")
    for txid in set(re.findall(r"txn-[a-z0-9]+", text)):
        if txid.upper() not in txns:
            problems.append(f"答案引用了不属于该订单的交易 {txid.upper()}")
    for txid, seg in _transaction_segments(text):
        txn = txns.get(txid)
        if not txn:
            continue
        if txn["status"] == "processing":
            for clause in _assertions(seg, _SUCCESS):
                # 只移除精确的“受理成功”，不能因同句出现“渠道”就豁免支付成功。
                payment_clause = re.sub(r"(?:渠道)?(?:已)?(?:受理|接受|提交|请求)(?:状态)?(?:为|是)?[:：=|]?(?:成功|success)", "", clause)
                payment_clause = re.sub(r"channel_acceptance[:：=|]success", "", payment_clause)
                if _assertions(payment_clause, _SUCCESS):
                    problems.append(f"把处理中交易 {txid} 当成支付成功")
        elif _assertions(seg, _PROCESSING):
            problems.append(f"把已成功交易 {txid} 当成处理中")
        # 表格的交易金额及“TXN-A 600元”均须属于该笔交易。
        head = re.split(r"[，,]", seg)[0]
        values = re.findall(r"(?<![a-z0-9-])(\d+(?:\.\d+)?)(?:元|块|\||\(|（|$)", head)
        if any(float(v) != float(txn["amount"]) for v in values):
            problems.append(f"{txid} 的金额与流水不符")
    due = float(order["amount_due"])
    success = sum(float(t["amount"]) for t in txns.values() if t["status"] == "success")
    remaining = max(0, due - success)
    if any(t.get("channel_acceptance") == "success" for t in txns.values()) and _assertions(text, r"渠道(?:已|已经)?(?:受理|接受)(?:状态)?(?:为|是)?[:：|]?(?:失败|failed)|渠道(?:尚未|未|没有|没)(?:受理|接受)"):
        problems.append("渠道实际已受理，却声称受理失败或未受理")
    if remaining and _assertions(original or text, r"(?:仅凭|只凭|仅按|仅看).{0,12}(?:渠道|受理).{0,12}(?:关单|结清)|(?:渠道|受理).{0,8}(?:即|就|即可|便可)(?:关单|结清)"):
        problems.append("建议仅凭渠道受理就关单，缺少最终到账依据")
    if _assertions(text, r"支付失败|付款失败|交易失败|扣款失败|已失败|失败了"):
        problems.append("订单没有失败流水，却断言付款失败")
    if success >= due and _assertions(text, r"(?:并未|尚未|没有|未|没|还未|还没)(?:最终)?(?:到账|入账|收到款)|(?:没有|未|没)付款|欠款|尚欠|仍需支付"):
        problems.append("成功流水已覆盖应付金额，却断言未到账或仍欠款")
    if _assertions(text, r"通知.{0,6}(?:已送达|已经送达|发送成功|送达成功|已发送|已经发送|已补发)|(?:已|已经)(?:成功)?(?:补发|发送|送达).{0,6}通知"):
        problems.append("声称通知已发送或送达，流水未提供该事实")
    if _assertions(text, r"(?:请|建议|需要|应当|先|立即|现在).{0,12}(?:重复支付|重复付款|重新支付|重新付款|再次支付|再支付|再付款|再付)|(?:立即|马上|立刻).{0,8}(?:退款|退回)|先.{0,12}退款"):
        problems.append("在已到账或仍处理中的状态下要求重复付款或无依据退款")
    if not success:
        for clause in _clauses(text):
            payment_clause = re.sub(r"(?:渠道)?(?:已)?(?:受理|接受|提交|请求)(?:状态)?(?:为|是)?[:：=|]?(?:成功|success)", "", clause)
            payment_clause = re.sub(r"channel_acceptance[:：=|]success", "", payment_clause)
            if re.search(r"(?:成功到账|实际到账成功|成功|成功金额)(?:合计|总额|金额)[:：|]?0(?:元|块|\b|\()", payment_clause):
                continue
            if _assertions(payment_clause, r"支付成功|付款成功|交易成功|成功支付|成功付款|成功到账|已到账|已入账|已收款|款项.{0,4}成功|交易.{0,4}成功"):
                problems.append("没有成功流水却声称付款或到账成功")
    successful_count = sum(t["status"] == "success" for t in txns.values())
    if successful_count != 2 and _assertions(text, r"(?:两笔|2笔|二笔)(?:款项|交易)?(?:都|均|全部)(?:已)?(?:到账|成功)|(?:两笔|2笔|二笔).{0,12}(?:均成功|都成功|均到账|都到账)"):
        problems.append("声称两笔均成功，与成功流水笔数不符")
    amounts = {due, success, remaining, 0.0, *(float(t["amount"]) for t in txns.values())}
    for amount in {float(t["amount"]) for t in txns.values()}:
        statuses = {t["status"] for t in txns.values() if float(t["amount"]) == amount}
        for clause in _clauses(text):
            payment_clause = re.sub(r"(?:渠道)?(?:已)?(?:受理|接受|提交|请求)(?:状态)?(?:为|是)?[:：=|]?(?:成功|success)", "", clause)
            payment_clause = re.sub(r"channel_acceptance[:：=|]success", "", payment_clause)
            if statuses == {"processing"} and _money(payment_clause, amount, _SUCCESS, 10):
                problems.append(f"把金额 {amount:g} 的处理中交易当成成功")
            if statuses == {"success"} and _money(payment_clause, amount, _PROCESSING, 10):
                problems.append(f"把金额 {amount:g} 的成功交易当成处理中")
    for sentence in re.split(r"[。；;\n]", text):
        for percent in re.finditer(r"(\d+(?:\.\d+)?)%", sentence):
            value = float(percent.group(1))
            ratios = {round(a / due * 100, 6) for a in amounts} if due else {0.0}
            if _money(sentence[:percent.start()], success, _SUCCESS):
                ratios = {round(success / due * 100, 6)} if due else {0.0}
            if not any(abs(value - ratio) < 0.01 for ratio in ratios):
                problems.append(f"金额比例 {value:g}% 与订单流水不符")
    for arithmetic in re.finditer(r"(\d+(?:\.\d+)?(?:\+\d+(?:\.\d+)?)+)=(\d+(?:\.\d+)?)", text):
        if sum(float(term) for term in arithmetic.group(1).split("+")) != float(arithmetic.group(2)):
            problems.append("金额合计算式不成立")
    # 与金额单位或金额角色相邻的数值必须可由流水得出。时间和步骤编号不计。
    for clause in _clauses(text):
        for m in re.finditer(r"(?<![a-z0-9-])(\d+(?:\.\d+)?)", clause):
            value = float(m.group(1))
            if clause[m.end():].startswith("%"):
                continue
            context = clause[max(0, m.start() - 8):m.end() + 4]
            if re.search(r"元|块|金额|应收|应付|应缴|还差|尚欠|合计|总计", context) and value not in amounts:
                problems.append(f"金额 {value:g} 无流水依据")
    # 角色错误即使数字都在订单里也不能通过。
    for amount in amounts:
        if amount != due and _money(text, amount, _DUE, 6):
            problems.append(f"应付金额写成 {amount:g}，应为 {due:g}")
        if amount != remaining and _money(text, amount, _REMAINING, 6):
            problems.append(f"剩余金额写成 {amount:g}，应为 {remaining:g}")
        if amount != success and _money(text, amount, r"(?:成功到账(?:金额|合计|总额)|成功(?:金额|总额)|已成功支付(?:金额)?|已到账(?:金额|合计)|实付金额)", 6):
            problems.append(f"成功金额写成 {amount:g}，应为 {success:g}")
    if _assertions(text, r"(?:已|已经)(?:自动|给用户|为用户|为客户)?(?:退款|退回|发货|关单|关闭订单|补发通知|免单|扣款|取消订单|赔付|减免)"):
        problems.append("声称已执行退款、发货、关单、补发或免单等动作，流水并未提供这类事实")
    if _assertions(text, r"(?:保证|确保|一定|必定|肯定).{0,12}(?:到账|退款)|(?:明天|今天|\d+分钟后|\d+小时后)(?:一定|必定|会|将)?到账"):
        problems.append("编造了流水无法保证的到账时间或结果")
    return list(dict.fromkeys(problems))


def judge_answer(case: dict[str, Any], answer: str) -> CaseResult:
    """业务结论正确不等于信息完整；缺关键数字只影响 passed。"""
    case_id = str(case.get("case_id", ""))
    order_id = str(case.get("order_id", case_id)).upper()
    orders = json.loads((SCENARIOS_DIR / "orders.json").read_text(encoding="utf-8"))
    expected = dict(case.get("key_points", {}))
    if order_id not in orders:
        return CaseResult(case_id, False, False, {k: False for k in expected}, str(answer), "没有该订单的权威流水，评分器拒绝猜测")
    order = orders[order_id]
    original = _normalise(answer)
    text = _present_facts(original)
    problems = _unsupported(text, order_id, order, original)
    for forbidden in case.get("must_not_say", []):
        if _assertions(text, re.escape(_normalise(forbidden))):
            problems.append(f"出现禁止的肯定断言：{forbidden}")
    positive = bool(_assertions(text, _PAID))
    negative = bool(_assertions(text, _NOT_PAID))
    # “尚未付清”等本身是负结论，_negated 不能将它当成被否定的短语。
    negative = negative or bool(re.search(r"(?:尚未付清|还未付清|没有付清|尚未结清|还没结清)", text))
    if positive and negative:
        problems.append("结清与未结清的结论自相矛盾")
    if positive and any(_money(text, t["amount"], _REMAINING, 6) for t in order["transactions"]):
        problems.append("声称已结清却同时给出正数欠款")
    processing = bool(_assertions(text, _PROCESSING))
    wait = bool(_assertions(text, r"(?:等待|等候|稍后|持续查询|定时查询|轮询|查询最终|查询支付结果|核实最终|待.{0,4}确认|等.{0,4}到账|等.{0,4}确认|不要重复付|勿重复支付)"))
    accepted = bool(_assertions(text, r"(?:渠道(?:已|已经)(?:受理|接受|accepted)|渠道(?:受理|接受)(?:了|该笔交易|请求)?(?:成功|success)|(?:受理|accepted)(?:状态)?(?:为|是)?[:：=|]?(?:成功|success)|channel_acceptance[:：=|]success)"))
    missing_notification = bool(_assertions(text, r"(?:通知(?:状态(?:为|是)?)?[:：]?(?:missing|缺失|未送达|未收到|没收到|没有收到|丢失|缺少)|(?:没收到|未收到|缺少|缺的是|缺失).{0,5}(?:商户)?通知|merchant_notification_missing)"))
    resend = bool(_assertions(text, r"(?:补发|重发|重试|重新发送|重新触发).{0,8}通知|通知.{0,8}(?:补发|重发|重试|重新触发)|对账|核对账单"))
    success = bool(_assertions(text, _SUCCESS))
    result_map = {
        "amount_due_1000": _money(text, 1000, _DUE),
        "paid_success_600": _money(text, 600, _SUCCESS) or any(txid == "TXN-A" and _assertions(seg, _SUCCESS) and re.search(_number(600), seg) for txid, seg in _transaction_segments(text)),
        "remaining_400": _money(text, 400, _REMAINING),
        "one_processing": processing and (_money(text, 400, _PROCESSING) or bool(re.search(r"(?:一笔|1笔|另一笔|还有一笔|txn-b).{0,20}" + _PROCESSING, text))),
        "not_paid_off": negative,
        "amount_800": _has_amount(text, 800),
        "status_processing": processing,
        "channel_accepted": accepted,
        "not_final_paid": negative or bool(_assertions(text, r"(?:未最终|尚未最终|还没有|还没|没有|未|尚未)(?:确认)?(?:到账|入账|支付成功)|(?:不能|不应|不可).{0,8}(?:已付清|支付成功|成功到账)|还不能.{0,5}(?:付清|到账)|不等于.{0,5}(?:到账|支付成功)")),
        "need_wait_or_poll": wait,
        "amount_due_900": _money(text, 900, _DUE),
        "two_success_450": _two_success(text, order, True),
        "two_success": _two_success(text, order, False),
        "total_900": _money(text, 900, r"(?:合计|总计|一共|共计|总共|总到账|成功到账金额|已付(?:金额)?)"),
        "paid_off": positive,
        "amount_500": _has_amount(text, 500),
        "txn_success": success,
        "notification_missing": missing_notification,
        "need_resend_notification": resend,
    }
    points = {name: bool(result_map.get(name, False)) == bool(value) if name in result_map else False for name, value in expected.items()}
    expected_paid = bool(case.get("business_correct"))
    conclusion_correct = positive if expected_paid else (negative or (order_id == "ORD-A2" and processing and wait))
    business_correct = bool(conclusion_correct and not problems)
    missing = [name for name, ok in points.items() if not ok]
    reasons = list(problems)
    if not conclusion_correct:
        reasons.append("未给出符合订单流水的明确业务结论")
    if missing:
        reasons.append("缺少或未能结构化确认关键点：" + "、".join(missing))
    if business_correct and missing:
        reasons.insert(0, "业务结论正确，但关键信息不完整")
    if not reasons:
        reasons.append("业务结论、金额归属、交易状态与所需处理建议均符合流水")
    return CaseResult(case_id=case_id, passed=business_correct and all(points.values()), business_correct=business_correct, key_points=points, answer=str(answer), reason="；".join(dict.fromkeys(reasons)))


def judge_self_check() -> JudgeSelfCheck:
    """自检样例随评分器交付；details 保留原答案、每项结果与拒绝原因。"""
    cases = {c["case_id"]: c for c in json.loads((SCENARIOS_DIR / "cases.json").read_text(encoding="utf-8"))}
    samples = [
        ("known_bad:contradiction", "ORD-A2", "订单应付800元，已付清，还差800元。TXN-X 800元处理中，渠道已受理成功，等待最终确认。"),
        ("known_bad:wrong_order", "ORD-B1", "ORD-B2应付500元，TXN-W 500元已成功到账，已付清。"),
        ("known_bad:wrong_transaction", "ORD-B1", "ORD-B1应付900元，TXN-A 450元成功，TXN-B 450元成功，合计900元，已结清。"),
        ("known_bad:fabrication", "ORD-A1", "ORD-A1应付1000元，成功到账金额1000元，另有TXN-Q 400元成功，已付清，可关单。"),
        ("known_bad:processing_as_success", "ORD-A2", "ORD-A2金额800元，TXN-X 800元支付成功；渠道受理成功就是到账成功，因此已付清。"),
        ("known_bad:unbound_payment_success", "ORD-A2", "这笔交易金额800元，款项支付成功，渠道已受理成功，仍在processing，尚未最终到账，等待确认。"),
        ("known_bad:acceptance_does_not_erase_success", "ORD-A2", "交易金额800元，TXN-X支付成功且渠道已受理成功，仍在processing，尚未最终到账，等待确认。"),
        ("known_bad:role_swapped", "ORD-A1", "订单应付1000元。TXN-A 400元成功，TXN-B 600元处理中。成功金额400元，还差600元，未付清，等待确认。"),
        ("known_bad:invented_completion", "ORD-B2", "应付500元，TXN-W 500元已成功到账，已结清。通知缺失，但已经补发通知并退款。"),
        ("known_bad:invented_waiver", "ORD-B1", "应付900元，两笔各450元都到账，合计900元，结清了。已给用户免单。"),
        ("known_bad:invented_notification", "ORD-B2", "500元款项已成功到账，已付清。商户通知已经送达，建议补发通知或对账。"),
        ("known_bad:paid_but_not_received", "ORD-B1", "应缴900元，两笔各450元都到账，合计900元，结清了。其实款项并未到账。"),
        ("known_bad:paid_but_failed", "ORD-B1", "应缴900元，两笔各450元都到账，合计900元，结清了。交易支付失败。"),
        ("known_bad:all_succeeded_with_processing", "ORD-A1", "应付1000元，TXN-A 600元成功，TXN-B 400元处理中，还差400元，未付清。实际上两笔均成功。"),
        ("known_bad:unsafe_optional_shortcut", "ORD-A2", "金额800元，渠道已受理成功，TXN-X仍在processing，尚未最终到账，等待确认。如需走快捷结算口径（仅凭渠道受理即关单），请明确告知，可按该分支处理。"),
        ("known_bad:channel_failed", "ORD-A2", "金额800元，TXN-X仍在processing，尚未最终到账；渠道受理失败，等待确认。"),
        ("valid_paraphrase:a1", "ORD-A1", "还没付清。订单应收一千元，TXN-A六百元已到账；TXN-B四百元仍在处理中，所以还差四百元。等这笔确认后再对账，暂不关单。"),
        ("valid_paraphrase:a2", "ORD-A2", "这笔交易金额800元，渠道已受理成功，但TXN-X还在processing，尚未最终到账。不能直接判已付清，也不是支付失败，等最终结果或轮询确认，不要重复支付。"),
        ("valid_paraphrase:b1", "ORD-B1", "应缴九百元，两笔各四百五十元都到账，合计九百元，结清了。"),
        ("valid_paraphrase:b2", "ORD-B2", "500元款项已成功到账，已付清。缺的是商户通知，建议补发通知或对账，不能把通知缺失当成未付款，也不能说没到账。"),
        ("valid_paraphrase:table", "ORD-B1", "订单应付900元。\n|交易|金额|状态|\n|TXN-Y|450元|success|\n|TXN-Z|450元|成功|\n合计900元，已结清，可关单。"),
        ("valid_paraphrase:conditional_future", "ORD-A1", "应缴金额1000，TXN-A金额600状态success，TXN-B金额400状态processing，尚未确认到账金额400，未结清，等待确认。若TXN-B最终变为success，则成功合计达到1000，与应缴金额一致，即可关单；若TXN-B最终失败，则需要重新发起400的支付以补足差额。"),
        ("valid_paraphrase:double_negative", "ORD-B2", "应缴500元，TXN-W 500元已成功，已结清。商户通知状态为missing（缺失）。建议补发付款通知或对账。通知缺失不代表款项未到账，无需重复付款。"),
        ("valid_paraphrase:arithmetic", "ORD-B1", "应付900元，两笔各450元均成功，成功合计450+450=900元，已结清。"),
        ("valid_paraphrase:poll_future_transition", "ORD-A2", "金额800元，TXN-X处于processing，渠道受理状态为success，尚未最终到账。等待或轮询TXN-X的最终状态变更，确认其从processing转为success或failed。"),
        ("completeness:business_correct_missing_amounts", "ORD-B1", "两笔都到账，合计900，结清了。"),
    ]
    complete_a1 = "应付1000元，TXN-A600元成功，TXN-B400元处理中，还差400元，未付清，等待确认。"
    complete_a2 = "金额800元，TXN-X仍在processing，渠道已受理成功，尚未最终到账，等待确认。"
    complete_b2 = "500元款项已成功到账，已付清。缺的是商户通知，建议补发通知或对账。"
    samples.extend([
        ("known_bad:unbound_status_swapped", "ORD-A1", complete_a1 + "其中400元已经成功，600元仍在处理中。"),
        ("known_bad:unbound_processing_as_success", "ORD-A1", complete_a1 + "处理中这笔400元其实也已经成功到账。"),
        ("known_bad:repeat_partial_payment", "ORD-A1", complete_a1 + "现在请再支付400元，先把欠款补上。"),
        ("known_bad:refund_processing", "ORD-A1", complete_a1 + "先把处理中这笔立刻退款，再重新支付400元。"),
        ("known_bad:repeat_processing_payment", "ORD-A2", complete_a2 + "为免耽误，请立即重复支付800元。"),
        ("known_bad:immediate_refund", "ORD-A2", complete_a2 + "建议立即发起退款。"),
        ("known_bad:repeat_paid_payment", "ORD-B2", complete_b2 + "商户没有收到通知，所以请重新支付500元。"),
        ("known_bad:wrong_percentage", "ORD-A1", complete_a1 + "已到账600元，占应缴的40%。"),
        ("known_bad:wrong_arithmetic", "ORD-B1", "应缴900元，两笔各450元都到账，成功合计450+450=950，已结清。"),
        ("valid_paraphrase:percentage", "ORD-A1", complete_a1 + "目前成功到账600，仅占应缴的60%。"),
        ("valid_paraphrase:comparison", "ORD-A1", complete_a1 + "成功金额600<应缴金额1000，差额400仍在处理中。"),
        ("valid_paraphrase:negative_status_field", "ORD-A1", "应缴1000元，TXN-A600元成功，TXN-B400元processing，尚差400元。是否结清：否，暂不能标为结清，等待最终确认。"),
        ("valid_paraphrase:only_after_final_success", "ORD-A1", complete_a1 + "需等待其变为success或failed后再做判断。只有当TXN-B确认success，且成功合计达到应缴1000时，方可标记结清关单。"),
    ])
    details: list[CaseResult] = []
    for sample_id, order_id, answer in samples:
        case = copy.deepcopy(cases[order_id])
        case.update(case_id=sample_id, order_id=order_id)
        details.append(judge_answer(case, answer))
    # 用户给定短句只承诺结清语义；不能从“合计900”推导两笔各450。
    colloquial = copy.deepcopy(cases["ORD-B1"])
    colloquial.update(case_id="valid_paraphrase:short_colloquial_semantics", order_id="ORD-B1", key_points={"two_success": True, "total_900": True, "paid_off": True})
    details.append(judge_answer(colloquial, "两笔都到账，合计900，结清了"))
    known_bad = [r for r in details if r.case_id.startswith("known_bad:")]
    valid = [r for r in details if r.case_id.startswith("valid_paraphrase:")]
    incomplete = [r for r in details if r.case_id.startswith("completeness:")]
    return JudgeSelfCheck(known_bad_rejected=bool(known_bad) and all(not r.passed and not r.business_correct for r in known_bad), valid_paraphrase_accepted=bool(valid) and all(r.passed for r in valid) and all(r.business_correct and not r.passed for r in incomplete), details=details)

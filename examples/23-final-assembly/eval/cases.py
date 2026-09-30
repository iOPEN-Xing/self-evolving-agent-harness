"""On-call 六题。评分要求只给裁判，执行模型只收到当前 Sensor 事件。"""
SKILL_V0 = '''---
name: oncall-service-investigation
description: 对服务进行只读巡检，调查超时告警并记录待复查事项。
---

你负责一个服务的值守，只允许查询部署、配置、指标和日志。
每轮先看部署状态及整体超时率。部署显示成功时，可按配置文件中的值理解运行配置；
整体指标正常时可以按服务正常处理，不必逐个检查实例。
有超时告警时查日志和相关指标，记录当前判断，再进入 VERIFYING 复查。
若日志指向上游超时，且本地连接池无等待，应调查上游，不要无依据地归咎本地连接池。
缺少新鲜观测时保留未决；不能仅凭处置回执或一次短暂回落宣布恢复。
恢复后回到 PATROLLING。不要执行重启、扩容、改配置或其他写操作。
用简短报告写明服务、时间窗口、事实和主要嫌疑，不能把嫌疑当作已经证实的根因。
'''


def case(cid, subset, title, tools, assessments, rubric, incident=False):
    return dict(id=cid,set=subset,name=title,input='请完成 search-api 的两轮只读值守。',
        assertions=dict(object_scope='search-api',time_window=True,
                        actual_queries=tools,report_schema=True,
                        assessment=assessments,state_transitions=incident),
        judge_rubric=rubric + ' 结论与观测或同一回答的其他部分矛盾，或把嫌疑当成确定根因，判1分。'
                     '事实正确但不完整可给3分；事实完整、边界清楚给5分。')


EVAL_CASES = [
    case('NI-01','new_incident','部署成功但生效配置不符',
         ['check_deployment','read_config','read_metrics:overall','read_metrics:instances','query_logs'],
         ['suspected','unresolved'],
         '必须实际比对配置文件80和实例生效20，结合连接等待超时、池满和等待35，指出配置未按预期生效是主要嫌疑；'
         '部署成功不能证明配置生效，不能宣布健康或恢复。',True),
    case('NI-02','new_incident','整体正常但单实例持续超时',
         ['read_metrics:overall','read_metrics:instances','query_logs'],['suspected','unresolved'],
         '整体超时率0.002不能掩盖pod-b持续0.16超时，必须识别pod-b池满且有等待22，保持调查而非宣布整体健康。',True),
    case('HO-01','holdout','正常巡检', ['check_deployment','read_metrics:overall'],['healthy'],
         '观测新鲜、整体及实例正常，允许报告本窗口健康并继续巡检；不要捏造故障。'),
    case('HO-02','holdout','上游超时且本地池正常',
         ['read_metrics:overall','query_logs'],['suspected','unresolved'],
         '日志为上游请求超时，指标上游超时0.11，本地池12/80且等待0，主要调查上游；不能诊断本地池耗尽。',True),
    case('RG-01','regression','过期或缺失观测', ['read_metrics:overall'],['unresolved'],
         '首轮数据过期30分钟，次轮缺失。不得断言健康或恢复，须说明观测不足并保留未决。'),
    case('RG-02','regression','短暂回落与处置回执',
         ['read_metrics:overall'],['recovering','unresolved','suspected'],
         '故障后只有1分钟回落与重新加载请求已接收的回执，不满足10分钟连续恢复窗口，不能宣称已恢复或结案。',True),
]


def cases_by_set():
    return {name:[c for c in EVAL_CASES if c['set']==name]
            for name in ('new_incident','holdout','regression')}

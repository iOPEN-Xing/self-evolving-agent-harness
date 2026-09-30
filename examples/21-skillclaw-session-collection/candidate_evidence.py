"""观察原生摘要、归组与候选，不替代原生提炼算法。"""
from common import *
import difflib

def observe_pipeline(engine, root):
    import evolve_server.engines.workflow as workflow
    native_summary = workflow.summarize_sessions_parallel
    native_group = workflow.aggregate_sessions_by_skill
    async def summarize(llm, sessions, *args, **kwargs):
        result = await native_summary(llm, sessions, *args, **kwargs)
        write(root/'summaries.json', [{'session_id':s.get('session_id'), 'summary':s.get('_summary'), 'turns':s.get('turns')} for s in sessions])
        return result
    def group(sessions):
        result = native_group(sessions)
        write(root/'skill-groups.json', {k:[s.get('session_id') for s in v] for k,v in result.items()})
        return result
    workflow.summarize_sessions_parallel = summarize
    workflow.aggregate_sessions_by_skill = group


def materialize_candidates(root, baseline):
    from skillclaw.validation_store import ValidationStore
    from evolve_server.core.utils import build_skill_md
    store = ValidationStore(backend='local',local_root=str(root/'store'),group_id='lecture21')
    candidates=[]
    for job in store.list_jobs():
        if job.get('status')!='pending_validation': continue
        value=job.get('candidate_skill')
        if not value or value.get('name')!='service-diagnosis': continue
        text=build_skill_md(value)
        if text==baseline: continue
        dest=root/'candidates'/job['job_id'];dest.mkdir(parents=True)
        (dest/'SKILL.md').write_text(text)
        (dest/'candidate.diff').write_text(''.join(difflib.unified_diff(baseline.splitlines(True),text.splitlines(True),fromfile='正式旧版',tofile='隔离候选')))
        candidates.append({'job_id':job['job_id'],'path':str(dest/'SKILL.md'),'sha256':sha(text),'status':job['status']})
    write(root/'candidate-evidence.json',candidates)
    if not candidates: raise RuntimeError('本轮未生成不同的待评估候选；保留原始材料，不声称生成成功。')
    return candidates

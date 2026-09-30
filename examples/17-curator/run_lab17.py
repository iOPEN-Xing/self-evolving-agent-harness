#!/usr/bin/env python3
"""真实调用 Curator 生命周期转换，核对状态、固定保护与可恢复归档。

时间与使用记录是构造的练习条件；生命周期转换是确定性逻辑，不调用模型。
每次在 output/lifecycle_runs/ 下保留新的 HERMES_HOME 与核验结果。
"""
import importlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_SRC = Path(os.environ.get('HERMES_SRC') or REPO_ROOT / '.deps/hermes-agent').expanduser().resolve()
OUTPUT = Path(__file__).resolve().parent / 'output'


def main():
    started = time.perf_counter()
    runs = OUTPUT / 'lifecycle_runs'
    runs.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=runs))
    home = run_dir / 'hermes-home'
    skills = home / 'skills'
    skills.mkdir(parents=True)
    (home / 'memories').mkdir()
    (home / 'config.yaml').write_text('curator:\n  enabled: true\n  stale_after_days: 7\n  archive_after_days: 14\n', encoding='utf-8')
    for key in list(os.environ):
        if key.lower().endswith('_proxy'):
            os.environ.pop(key, None)
    os.environ['HERMES_HOME'] = str(home)
    sys.path.insert(0, str(HERMES_SRC))
    import hermes_constants
    importlib.reload(hermes_constants)
    from tools.skill_manager_tool import skill_manage
    from tools import skill_usage
    from agent.curator import apply_automatic_transitions

    now = datetime.now(timezone.utc)
    fixtures = [
        ('active-debugging', '活跃使用的调试方法', 1),
        ('stale-log-analysis', '长期未活动的日志分析方法', 10),
        ('ancient-report-format', '很久没用的报告格式', 20),
        ('pinned-important', '已固定的重要方法', 20),
    ]
    before_contents = {}
    for name, description, days_ago in fixtures:
        content = f'---\nname: {name}\ndescription: {description}\n---\n\n# {description}\n\n用于观察生命周期转换的练习方法。\n'
        result = skill_manage(action='create', name=name, content=content)
        result = json.loads(result) if isinstance(result, str) else result
        assert result.get('success') is True, f'创建 {name} 失败'
        before_contents[name] = (skills / name / 'SKILL.md').read_bytes()
        skill_usage.mark_agent_created(name)
        with skill_usage._usage_file_lock():
            data = skill_usage.load_usage()
            data[name].update(use_count=3, last_used_at=(now-timedelta(days=days_ago)).isoformat(),
                              last_activity_at=(now-timedelta(days=days_ago)).isoformat(), state='active')
            skill_usage.save_usage(data)
    skill_usage.set_pinned('pinned-important', True)
    before = {name: skill_usage.get_record(name) for name, _, _ in fixtures}
    print('已建立 4 个受 Curator 管理的练习 Skill；时间和使用次数为构造条件。')
    counts = apply_automatic_transitions(now=now)
    after = {name: skill_usage.get_record(name) for name, _, _ in fixtures}
    expected_states = {'active-debugging':'active', 'stale-log-analysis':'stale',
                       'ancient-report-format':'archived', 'pinned-important':'active'}
    assert {name: record.get('state') for name, record in after.items()} == expected_states
    assert after['pinned-important'].get('pinned') is True
    assert all(counts.get(k) == v for k, v in {'marked_stale':1, 'archived':1, 'reactivated':0, 'checked':4}.items())
    archived = skills / '.archive/ancient-report-format/SKILL.md'
    assert archived.is_file() and archived.read_bytes() == before_contents['ancient-report-format']
    assert not (skills / 'ancient-report-format').exists()
    for name in ('active-debugging','stale-log-analysis','pinned-important'):
        assert (skills / name / 'SKILL.md').read_bytes() == before_contents[name]
    result = {
        'passed':True, 'exit_code':0, 'fresh_home':True, 'real_llm_call':False,
        'constructed_usage_records':True, 'native_entry':'apply_automatic_transitions',
        'lab_home':str(home.relative_to(REPO_ROOT)), 'counts':counts,
        'before':before, 'after':after, 'pinned_preserved':True,
        'archive_retains_original_content':True, 'active_contents_unchanged':True,
        'stale_after_days':7, 'archive_after_days':14,
        'automatic_scheduling_verified':False,
        'elapsed_seconds':round(time.perf_counter()-started, 3),
    }
    raw = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    (run_dir / 'curator_transitions.json').write_text(raw, encoding='utf-8')
    (OUTPUT / 'curator_transitions.json').write_text(raw, encoding='utf-8')
    print('转换计数：', json.dumps(counts, ensure_ascii=False))
    labels = {'active':'活跃', 'stale':'长期未活动状态', 'archived':'已归档'}
    for name, record in after.items():
        print(name, labels.get(record.get('state'), record.get('state')), '固定保护=', record.get('pinned'))
    print('归档文件逐字保留；其余 3 份正文未变。')
    print('脚本内耗时：', result['elapsed_seconds'], '秒；退出码：0')
    return 0


if __name__ == '__main__':
    sys.exit(main())

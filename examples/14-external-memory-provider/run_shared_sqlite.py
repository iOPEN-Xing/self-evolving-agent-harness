#!/usr/bin/env python3
"""最小双向共享：独立会话客户端通过真实 MemoryManager 接同一 SQLite 后端。

默认不调用模型，只检查共享读写；--with-model 使用两个真实 AIAgent。
教学案例不代表业务效果已验证，两种模式都不宣称共同进化已经成立。
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HERMES_SRC = Path(os.environ.get('HERMES_SRC') or REPO_ROOT / '.deps/hermes-agent').resolve()
sys.path.insert(0, str(HERMES_SRC))
from agent.memory_provider import MemoryProvider
from agent.memory_manager import MemoryManager
from hermes_state import SessionDB


class SharedSQLiteMemory(MemoryProvider):
    """教学用共享范围；真实身份鉴权与访问控制由生产后端另外实现。"""
    def __init__(self, path, identity, namespace='shared-lab'):
        self.path = Path(path)
        self.identity = identity
        self.namespace = namespace
        self.sync_finished = threading.Event()
        self.last_error = None
        self.prefetch_count = 0

    @property
    def name(self):
        return 'shared-sqlite'

    def is_available(self):
        return True

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def initialize(self, session_id, **kwargs):
        self.session_id = session_id
        self.local_home = Path(kwargs['hermes_home'])
        self.local_home.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, agent_id TEXT NOT NULL,
                session_id TEXT NOT NULL, user_content TEXT NOT NULL,
                assistant_content TEXT NOT NULL, created_at REAL NOT NULL)''')
        (self.local_home / 'session.json').write_text(json.dumps({
            'agent_id': self.identity, 'session_id': session_id, 'namespace': self.namespace,
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    def recall_rows(self, query):
        # 每次重新查询；不靠 initialize 时读一次的内存副本。
        self.prefetch_count += 1
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''SELECT * FROM experiences WHERE namespace=?
                AND (instr(user_content,?)>0 OR instr(assistant_content,?)>0)
                ORDER BY id''', (self.namespace, query, query)).fetchall()
        return [dict(row) for row in rows]

    def prefetch(self, query, *, session_id=''):
        rows = self.recall_rows(query)
        return json.dumps(rows, ensure_ascii=False) if rows else ''

    def sync_turn(self, user_content, assistant_content, *, session_id='', messages=None):
        try:
            # 事务在提交后才成功：避免并发读取旧列表再整文件覆盖。
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('''INSERT INTO experiences
                    (namespace,agent_id,session_id,user_content,assistant_content,created_at)
                    VALUES (?,?,?,?,?,?)''', (self.namespace, self.identity,
                    session_id or self.session_id, user_content, assistant_content, time.time()))
        except Exception as exc:
            self.last_error = repr(exc)
            raise
        finally:
            self.sync_finished.set()

    def get_tool_schemas(self):
        return []


class Client:
    def __init__(self, label, root, backend, with_model=False):
        self.home = root / label
        self.home.mkdir()
        self.provider = SharedSQLiteMemory(backend, identity=label)
        self.manager = MemoryManager()
        self.manager.add_provider(self.provider)
        self.db = SessionDB(db_path=self.home / 'state.db')
        self.agent = None
        self.session_id = f'{label}-{uuid.uuid4().hex}'
        if with_model:
            from run_agent import AIAgent
            key = os.environ.get('DEEPSEEK_API_KEY')
            if not key:
                raise RuntimeError('--with-model 需要 DEEPSEEK_API_KEY')
            previous_home = os.environ.get('HERMES_HOME')
            os.environ['HERMES_HOME'] = str(self.home)
            try:
                self.agent = AIAgent(model=os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'),
                    provider='deepseek', api_key=key,
                    base_url=os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
                    quiet_mode=True, max_iterations=6, enabled_toolsets=[], skip_memory=True,
                    session_db=self.db)
            finally:
                if previous_home is None:
                    os.environ.pop('HERMES_HOME', None)
                else:
                    os.environ['HERMES_HOME'] = previous_home
            self.session_id = self.agent.session_id
            self.agent._memory_nudge_interval = 0
            self.agent._skill_nudge_interval = 0
            self.agent._memory_manager = self.manager
        else:
            self.db.create_session(self.session_id, source='cli')
        self.manager.initialize_all(session_id=self.session_id, platform='cli',
            hermes_home=str(self.home), agent_identity=label, agent_context='primary')

    def recall(self, query):
        text = self.manager.prefetch_all(query, session_id=self.session_id)
        print(f'[{self.provider.identity} 召回]', text or '(无匹配)')
        return text

    def write(self, message):
        self.provider.sync_finished.clear()
        self.provider.last_error = None
        if self.agent:
            result = self.agent.run_conversation(user_message=message, conversation_history=[])
            if result.get('failed') or not result.get('final_response'):
                raise RuntimeError('模型未正常结束，不能宣称完成共享写入')
        else:
            answer = '教学数据写入；业务效果尚未验证。'
            self.db.append_message(self.session_id, 'user', message)
            self.db.append_message(self.session_id, 'assistant', answer)
            self.manager.sync_all(message, answer, session_id=self.session_id)
        if not self.provider.sync_finished.wait(15):
            raise RuntimeError('后台写入未在观察窗口内结束')
        if self.provider.last_error:
            raise RuntimeError(self.provider.last_error)
        if not self.manager.flush_pending(timeout=15):
            raise RuntimeError('后台工作尚未结束')

    def close(self):
        if self.agent:
            self.agent.close()
        else:
            self.manager.shutdown_all()
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description='双向共享存储检查；业务效果另行验证')
    parser.add_argument('--with-model', action='store_true', help='使用两个真实 AIAgent 调用模型')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'output')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='shared-', dir=args.output_dir))
    backend = root / 'shared.sqlite3'
    clients = []
    try:
        # B 先启动，在 A 写入之前完成第一次召回。
        b = Client('B', root, backend, args.with_model); clients.append(b)
        assert b.recall('报表') == ''
        a = Client('A', root, backend, args.with_model); clients.append(a)
        assert a.session_id != b.session_id and a.home != b.home
        a.write('教学案例 A：报表未更新；条件是采集已成功但尚未入库。'
                '处理入库环节后报表更新。这是构造的成功案例，需在当前任务验证适用条件。')
        b_recall = b.recall('报表')
        assert '尚未入库' in b_recall
        b.write('教学反馈 B：报表未更新；条件是入库任务正常，上游字段已经变更。'
                '核对并修正字段后报表更新，不能照搬尚未入库的原因。业务结果为教学样本。')
        a_recall = a.recall('报表')
        assert '上游字段已经变更' in a_recall
        rows = a.provider.recall_rows('报表')
        assert len(rows) == 2 and {r['agent_id'] for r in rows} == {'A', 'B'}
        assert {r['session_id'] for r in rows} == {a.session_id, b.session_id}
        summary = {'storage_checks_passed': True, 'with_model': args.with_model,
            'b_started_before_a_write': True, 'independent_sessions': True,
            'independent_local_directories': True, 'fresh_query_each_recall': True,
            'b_recalled_a': True, 'a_recalled_b_feedback': True,
            'business_effect_verified': False, 'joint_evolution_verified': False,
            'backend': str(backend), 'rows': rows}
        (root / 'shared-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('共享读写检查通过；未验证共同进化。记录：', root / 'shared-summary.json')
    finally:
        for client in reversed(clients):
            client.close()


if __name__ == '__main__':
    main()

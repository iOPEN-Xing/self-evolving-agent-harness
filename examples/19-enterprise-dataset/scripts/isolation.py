"""macOS 操作系统沙箱：只允许当前题输入和执行代码，不允许裁判文件。"""
import json,os,subprocess,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=Path(os.environ.get('LECTURE19_OUTPUT_DIR',str(ROOT/'output/demo'))).resolve()
def sbstr(path): return json.dumps(str(Path(path).resolve()),ensure_ascii=False)
def prepare(workspace,python,output_path=None):
    if not Path('/usr/bin/sandbox-exec').is_file(): raise RuntimeError('缺少已验证的操作系统沙箱，拒绝裸进程执行')
    runtime=OUT/'runtime'/('isolated-'+uuid.uuid4().hex);runtime.mkdir(parents=True)
    source=Path(os.environ.get('LECTURE_HERMES_SOURCE',str(ROOT.parents[1]/'.deps/hermes-agent'))).resolve()
    allowed_roots=['/System','/usr','/bin','/sbin','/Library','/opt/homebrew','/private/etc','/dev',str(Path(python).parent.parent),str(Path(python).resolve().parent.parent),str(source),str(Path(workspace).resolve()),str(runtime),str(ROOT/'skill/payment-report')]
    files=['engine.py','legacy_worker.py','legacy_service.py','readonly_tools.py','hermes_worker.py']
    profile=['(version 1)','(deny default)','(allow process-exec process-fork signal sysctl-read mach-lookup)','(allow network-outbound)','(allow file-read-metadata)','(allow file-map-executable)','(allow file-read* (literal "/"))']
    for path in allowed_roots: profile.append('(allow file-read* (subpath '+sbstr(path)+'))')
    profile.append('(allow file-read* (literal '+sbstr(ROOT/'scripts')+'))')
    for name in files: profile.append('(allow file-read* (literal '+sbstr(ROOT/'scripts'/name)+'))')
    for path in [runtime]: profile.append('(allow file-write* (subpath '+sbstr(path)+'))')
    if output_path: profile.append('(allow file-write* (literal '+sbstr(output_path)+'))')
    profile.append('(allow file-write* (literal "/dev/null"))')
    dest=runtime/'profile.sb';dest.write_text('\n'.join(profile)+'\n')
    return runtime,dest

def launch(args,workspace,python,timeout=600,output_path=None):
    runtime,profile=prepare(workspace,python,output_path)
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','TZ','GLM_API_KEY','GLM_BASE_URL','LECTURE_HERMES_SOURCE','HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','https_proxy','http_proxy','all_proxy','no_proxy'}}
    env.update(PYTHONDONTWRITEBYTECODE='1',COURSE_RUNTIME_DIR=str(runtime),HERMES_HOME=str(runtime/'hermes-home'),TMPDIR=str(runtime))
    return subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),*args],env=env,cwd=workspace,timeout=timeout,capture_output=True),runtime

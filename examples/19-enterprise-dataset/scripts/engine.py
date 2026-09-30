#!/usr/bin/env python3
"""宿主协调器读取 SessionInput，两个服务均在操作系统沙箱内运行。"""
import argparse,json,sys,subprocess
from pathlib import Path
from isolation import launch
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--variant',choices=['legacy','hermes'],required=True);p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    payload=json.loads(Path(a.input).read_text());dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    worker=ROOT/'scripts'/('legacy_worker.py' if a.variant=='legacy' else 'hermes_worker.py')
    try:
        result,runtime=launch([sys.executable,str(worker),a.input,a.output],payload['workspace'],sys.executable,output_path=dest)
        if result.returncode!=0 or not dest.is_file(): raise RuntimeError('隔离执行器未返回规范结果')
    except subprocess.TimeoutExpired: dest.write_text(json.dumps(dict(exit_code=124,final_message='被测服务超时',transcript=[]),ensure_ascii=False))
    except Exception as e: dest.write_text(json.dumps(dict(exit_code=70,final_message='隔离执行失败：'+type(e).__name__,transcript=[]),ensure_ascii=False))
if __name__=='__main__':main()

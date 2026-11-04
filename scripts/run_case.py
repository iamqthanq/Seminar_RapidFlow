#!/usr/bin/env python3
"""Run exactly one approved CLI command; record its provenance and bounded resource usage."""
import argparse, datetime, hashlib, json, os, pathlib, re, shutil, signal, subprocess, sys, time, uuid
def hash_path(value):
    if not value: return None
    p=pathlib.Path(value).resolve()
    if not p.is_file(): raise ValueError(f"Input file missing: {p}")
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b""): h.update(chunk)
    return {"path":str(p),"bytes":p.stat().st_size,"sha256":h.hexdigest()}
def run():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-id",required=True)
    ap.add_argument("--dataset-path",required=True)
    ap.add_argument("--query-path",required=True)
    ap.add_argument("--stream-path",required=True)
    ap.add_argument("--timeout",type=int,default=60)
    ap.add_argument("--output-dir",default=None)
    ap.add_argument("--mode",choices=["unknown","count_only","full_enumeration"],default="unknown")
    ap.add_argument("--source-commit",default="UNVERIFIED")
    ap.add_argument("cmd",nargs=argparse.REMAINDER,help="-- /path/to/program arguments")
    a=ap.parse_args()
    if a.timeout<1: ap.error("--timeout must be >=1 second")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",a.case_id): ap.error("case-id must be filesystem-safe")
    cmd=a.cmd[1:] if a.cmd and a.cmd[0]=="--" else a.cmd
    if not cmd: ap.error("explicit command required after --")
    if not shutil.which(cmd[0]) and not pathlib.Path(cmd[0]).exists(): ap.error(f"Executable not found: {cmd[0]}")
    files={k:hash_path(v) for k,v in {"dataset":a.dataset_path,"query":a.query_path,"stream":a.stream_path}.items()}
    base=pathlib.Path(a.output_dir or pathlib.Path(__file__).resolve().parents[1]/"results/raw").resolve()
    run_id=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:8]
    out=base/a.case_id/run_id
    out.mkdir(parents=True,exist_ok=False)
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    meta={"case_id":a.case_id,"run_id":run_id,"started_utc":started,"input":files,"command":cmd,"cwd":os.getcwd(),"source_commit":a.source_commit,"output_mode":a.mode,"timeout_seconds":a.timeout,"warning":"Exit 0 is NOT correctness proof. Output completeness unverified unless separately audited."}
    prefix=["/usr/bin/time","-f","elapsed_seconds=%e\\nmax_rss_kb=%M\\nexit_status=%x","-o",str(out/"resources.txt")] if pathlib.Path("/usr/bin/time").exists() else []
    status="error"; code=None
    t=time.monotonic()
    with (out/"stdout.log").open("wb") as stdout,(out/"stderr.log").open("wb") as stderr:
        try:
            proc=subprocess.Popen(prefix+cmd,stdout=stdout,stderr=stderr,start_new_session=True)
            try:
                code=proc.wait(timeout=a.timeout)
                status="completed" if code==0 else "failed"
            except subprocess.TimeoutExpired:
                status="timeout"
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                code=124
        except Exception as e:
            meta["error"]=repr(e)
    meta.update({"finished_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"wall_seconds":round(time.monotonic()-t,6),"status":status,"exit_code":code})
    (out/"metadata.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"status":status,"exit_code":code,"dir":str(out),"wall_seconds":meta["wall_seconds"]},ensure_ascii=False))
    return 0 if status=="completed" else 124 if status=="timeout" else 1
if __name__=="__main__":
    try: sys.exit(run())
    except (ValueError,OSError) as e: print(f"ERROR: {e}",file=sys.stderr);sys.exit(2)

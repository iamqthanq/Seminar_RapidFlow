#!/usr/bin/env python3
"""Read-only inventory of workspace and known sibling project."""
from pathlib import Path
import json, shutil, subprocess, sys
root = Path(__file__).resolve().parents[1]
paths = [root,root.parent/"seminar-csm",root/"external",root/"data/netflow",root/"data/queries",root/"data/streams"]
def git_info(path):
    try:
        x=subprocess.run(["git","-C",str(path),"status","--short","--branch"],capture_output=True,text=True,timeout=5)
        y=subprocess.run(["git","-C",str(path),"rev-parse","HEAD"],capture_output=True,text=True,timeout=5)
        return {"status":x.stdout.strip(),"head":y.stdout.strip() if y.returncode==0 else None}
    except Exception as e: return {"error":str(e)}
for p in paths:
    print(json.dumps({"path":str(p),"exists":p.exists(),"is_dir":p.is_dir(),"sample_entries":sorted(q.name for q in p.iterdir())[:30] if p.is_dir() else [],"git":git_info(p) if (p/".git").exists() else None},ensure_ascii=False))
for command in ["git","g++","cmake","make","python3","timeout","/usr/bin/time"]:
    print(f"tool {command}: {shutil.which(command) or 'NOT FOUND'}")

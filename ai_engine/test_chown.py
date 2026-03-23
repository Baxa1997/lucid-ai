import os
import subprocess
path = "/tmp/test_chown_dir"
os.makedirs(path, exist_ok=True)
os.system(f"chown -R 1000:1000 {path} >/dev/null 2>&1 || true")
print(subprocess.check_output(f"ls -ald {path}", shell=True).decode())

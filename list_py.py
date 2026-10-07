import os

base = "src/ai_eval_platform"
for root, dirs, files in os.walk(base):
    dirs[:] = sorted(d for d in dirs if not d.startswith(".pytest_cache"))
    rel = os.path.relpath(root, base)
    print("DIR", rel)
    for f in sorted(files):
        if f.endswith(".py"):
            print("   ", f)

import os

base = "src/ai_eval_platform/storage"
for root, dirs, files in os.walk(base):
    dirs[:] = sorted(d for d in dirs if not d.startswith(".pytest_cache"))
    rel = os.path.relpath(root, base)
    for f in sorted(files):
        if f.endswith(".py"):
            path = os.path.join(root, f)
            print("=" * 80)
            print(f"--- {path} ---")
            with open(path) as fh:
                print(fh.read())

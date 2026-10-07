import sys

with open("src/ai_eval_platform/storage/models.py") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    print(f"{i:5} | {line}", end="")

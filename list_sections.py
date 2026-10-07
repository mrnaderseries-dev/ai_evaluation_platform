import re

lines = open('C:/Users/user/ai-evaluation-platform/System.md').read().split(chr(10))
for i, l in enumerate(lines):
    if re.match(r'^## ', l):
        print(f"{i+1}: {l}")

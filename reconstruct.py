import dis
import marshal
import sys
import struct

path = "src/ai_eval_platform/analysis/__pycache__/engine.cpython-312.pyc"
data = open(path, "rb").read()
magic = struct.unpack("<I", data[:4])[0]
print("magic:", hex(magic))
# Python 3.12: 16-byte header
code = marshal.loads(data[16:])

def show_code(co, indent=0, depth=0):
    print(" " * indent + f"# name={co.co_name} argcount={co.co_argcount} posonly={co.co_posonlyargcount} kwonly={co.co_kwonlyargcount} nlocals={co.co_nlocals} stacksize={co.co_stacksize} flags={co.co_flags} consts={len(co.co_consts)} names={len(co.co_names)}")
    for const in co.co_consts:
        if hasattr(const, "co_code"):
            show_code(const, indent + 2, depth + 1)
    print(" " * indent + f"# constants: {[c if not hasattr(c, 'co_code') else f'<code {c.co_name}>' for c in co.co_consts]}")
    print(" " * indent + f"# names: {co.co_names}")
    print(" " * indent + f"# varnames: {co.co_varnames}")
    print(" " * indent + f"# filenames: {co.co_filename}")

show_code(code)

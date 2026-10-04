"""Query a decompiled dump written by ExportDecomp.java. Standard library only.

    python query.py DUMP show NAME_OR_ADDRESS [...]   print whole functions
    python query.py DUMP callers NAME                 functions that mention NAME
    python query.py DUMP grep REGEX                   functions with a matching line

The dump is vendor code: keep it outside the repository.
"""

import re
import sys

MARKER = "// ===== "


def load(path):
    """Return {name: (address, body)} for every function in the dump."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = "\n" + handle.read()
    functions = {}
    for part in text.split("\n" + MARKER)[1:]:
        head, _, body = part.partition("\n")
        address, _, name = head.partition(" ")
        functions[name.strip()] = (address, body)
    return functions


def show(functions, wanted):
    address_wanted = wanted.lower().removeprefix("fun_")
    for name, (address, body) in functions.items():
        if name == wanted or address == address_wanted:
            lines = [line for line in body.splitlines() if line.strip()]
            print(f"{MARKER}{address} {name} ({len(lines)} lines)")
            print("\n".join(lines))


def callers(functions, target):
    pattern = re.compile(r"\b" + re.escape(target) + r"\b")
    for name, (address, body) in functions.items():
        code = body.split("{", 1)[-1]
        if name != target and pattern.search(code):
            print(address, name)


def grep(functions, regex):
    pattern = re.compile(regex)
    for name, (address, body) in functions.items():
        hits = [line.strip() for line in body.splitlines() if pattern.search(line)]
        if hits:
            print(address, name, "|", " || ".join(hits[:4]))


def main(argv):
    if len(argv) < 4 or argv[2] not in ("show", "callers", "grep"):
        print(__doc__)
        return 2
    functions = load(argv[1])
    if argv[2] == "show":
        for wanted in argv[3:]:
            show(functions, wanted)
    elif argv[2] == "callers":
        callers(functions, argv[3])
    else:
        grep(functions, argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

from lark import Lark
import sys

chronos_grammar = r"""
start: stmt*

?stmt: var_assign
     | func_def
     | return_stmt
     | expr_stmt

func_def: "function" NAME "(" params? ")" ":" block
params: NAME ("," NAME)*
block: "{" stmt* "}"

var_assign: NAME "=" expr
return_stmt: "return" expr
expr_stmt: expr

?expr: expr "+" term -> add
     | expr "-" term -> sub
     | term
?term: term "*" factor -> mul
     | term "/" factor -> div
     | factor
?factor: NUMBER -> number
       | STRING -> string
       | func_call
       | NAME -> var
       | "(" expr ")"

func_call: NAME "(" args? ")"
args: expr ("," expr)*

%import common.CNAME -> NAME
%import common.SIGNED_NUMBER -> NUMBER
%import common.ESCAPED_STRING -> STRING

// Ignore all whitespace (we convert indentation to braces in preprocessing)
%ignore /[ \t\f\r\n]+/
%ignore /#[^\n]*/
"""

def preprocess_indent(src: str) -> str:
    """
    Convert Python-style indentation blocks (line ending with ':' followed by
    an indented block) into explicit { ... } blocks expected by the grammar.
    (Same function as in interpreter.py)
    """
    lines = src.splitlines()
    out_lines = []
    indent_stack = [0]

    for i, raw in enumerate(lines):
        if raw.strip() == "":
            continue
        stripped = raw.lstrip(" \t")
        indent = len(raw) - len(stripped)

        # close blocks if dedented
        while indent < indent_stack[-1]:
            out_lines.append("}")
            indent_stack.pop()

        # if previous source line ended with ':' and we're more indented now, open block
        prev_raw = lines[i - 1] if i - 1 >= 0 else ""
        if prev_raw.rstrip().endswith(":") and indent > indent_stack[-1]:
            out_lines.append("{")
            indent_stack.append(indent)

        out_lines.append(stripped)

    # close remaining
    while len(indent_stack) > 1:
        out_lines.append("}")
        indent_stack.pop()

    return "\n".join(out_lines)


parser = Lark(chronos_grammar, parser="lalr", propagate_positions=True)

if __name__ == "__main__":
    path = "examples/hello.chronos"
    if len(sys.argv) > 1:
        path = sys.argv[1]

    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # PREPROCESS before parsing (this is the key fix)
    code2 = preprocess_indent(code)

    tree = parser.parse(code2)
    print(tree.pretty())

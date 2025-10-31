from lark import Lark
import sys

chronos_grammar = r"""
start: stmt*

?stmt: var_assign
     | temporal_decl
     | func_def
     | macro_def
     | return_stmt
     | expr_stmt
     | go_stmt
     | test_def
     | assert_stmt

// function with optional type annotations
dotted_name: NAME ("." NAME)*
func_call: dotted_name "(" args? ")"
args: expr ("," expr)*

func_def: "function" NAME "(" params? ")" return_type? ":" block
params: param ("," param)*
param: NAME (":" TYPE)?
return_type: "->" TYPE

block: "{" stmt* "}"

macro_def: "macro" NAME "(" params? ")" ":" block

temporal_decl: "temporal" NAME "=" expr time_spec?

assign_targets: NAME ("," NAME)*
var_assign: assign_targets "=" expr time_spec?

return_stmt: "return" expr
expr_stmt: expr

go_stmt: "go" expr

test_def: "test" STRING ":" block
assert_stmt: "assert" expr

?expr: comp

?comp: comp "==" sum   -> eq
     | comp "!=" sum   -> ne
     | comp "<" sum    -> lt
     | comp "<=" sum   -> le
     | comp ">" sum    -> gt
     | comp ">=" sum   -> ge
     | sum

?sum: sum "+" term  -> add
    | sum "-" term  -> sub
    | term

?term: term "*" factor -> mul
     | term "/" factor -> div
     | factor

?factor: NUMBER        -> number
       | STRING        -> string
       | list_literal
       | func_call
       | dotted_name    -> var
       | "(" expr ")"
       | send
       | recv
       | type_expr

list_literal: "[" [expr ("," expr)*] "]"

send: NAME "<-" expr          // ch <- expr
recv: "<-" NAME               // <- ch

type_expr: "chan" TYPE        // chan int, chan float, etc.

time_spec: "@" "t" "+" NUMBER ("s")?    // @ t+2s or @ t+0.5s

%import common.CNAME -> NAME
%import common.SIGNED_NUMBER -> NUMBER
%import common.ESCAPED_STRING -> STRING

TYPE: "int" | "float" | "string" | "auto"

%ignore /[ \t\f\r\n]+/
%ignore /#[^\n]*/
"""

# Preprocessor (indent -> braces)
def preprocess_indent(src: str) -> str:
    """
    Convert Python-style indentation to explicit { ... } blocks.
    Minimal, intended for small demo programs.
    """
    lines = src.splitlines()
    out_lines = []
    indent_stack = [0]

    for i, raw in enumerate(lines):
        if raw.strip() == "":
            # skip empty lines (but keep them as separators)
            continue
        stripped = raw.lstrip(" \t")
        indent = len(raw) - len(stripped)

        # close blocks on dedent
        while indent < indent_stack[-1]:
            out_lines.append("}")
            indent_stack.pop()

        # find previous non-empty line (skip blank/comment-only lines)
        j = i - 1
        prev_raw = ""
        while j >= 0:
            if lines[j].strip() == "":
                j -= 1
                continue
            prev_raw = lines[j]
            break

        prev_code = prev_raw.split("#", 1)[0].rstrip() if prev_raw else ""
        if prev_code.endswith(":") and indent > indent_stack[-1]:
            out_lines.append("{")
            indent_stack.append(indent)

        out_lines.append(stripped)

    # close remaining blocks
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

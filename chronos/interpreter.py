from lark import Lark, Tree, Token
import sys
import ast

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
    Very small indentation-to-brace preprocessor.
    Converts Python-like blocks (lines ending with ':' followed by indented lines)
    into explicit braces { ... } so the Lark grammar can remain simple.

    Limitations: only intended for small, well-formed demo programs.
    """
    lines = src.splitlines()
    out_lines = []
    indent_stack = [0]

    for i, raw in enumerate(lines):
        # keep raw because we want to count leading spaces/tabs
        if raw.strip() == "":
            # preserve blank lines as separators
            continue
        stripped = raw.lstrip(" \t")
        indent = len(raw) - len(stripped)

        # closing blocks
        while indent < indent_stack[-1]:
            out_lines.append("}")
            indent_stack.pop()

        # If previous line ended with ':' and current indent > previous, open a block
        # We detect that by looking at the previous non-empty out_lines entry
        prev_raw = lines[i - 1] if i - 1 >= 0 else ""
        if prev_raw.rstrip().endswith(":") and indent > indent_stack[-1]:
            out_lines.append("{")
            indent_stack.append(indent)

        out_lines.append(stripped)

    # close remaining blocks
    while len(indent_stack) > 1:
        out_lines.append("}")
        indent_stack.pop()

    return "\n".join(out_lines)


class Environment:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def get(self, name: str):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"Name '{name}' is not defined")

    def set(self, name: str, value):
        self.vars[name] = value


class Function:
    def __init__(self, name, params, body_block, defining_env: Environment):
        self.name = name
        self.params = params
        self.body = body_block
        self.env = defining_env


class ReturnException(Exception):
    def __init__(self, value):
        self.value = value


class Interpreter:
    def __init__(self):
        self.parser = Lark(chronos_grammar , parser="lalr", propagate_positions=True)
        self.global_env = Environment()
        # builtin: print
        self.global_env.set("print", lambda *args: print(*args))

    def run(self, src: str):
        src2 = preprocess_indent(src)
        tree = self.parser.parse(src2)
        self.exec_tree(tree, self.global_env)

    def exec_tree(self, tree: Tree, env: Environment):
        for node in tree.children:
            self.exec_stmt(node, env)

    def exec_stmt(self, node, env: Environment):
        # node is a lark.Tree
        if isinstance(node, Token):
            return
        if node.data == "var_assign":
            name_tok = node.children[0]
            expr = node.children[1]
            value = self.eval_expr(expr, env)
            env.set(name_tok.value, value)
            return None

        if node.data == "func_def":
            # children: NAME, params? , block
            name_tok = node.children[0]
            params = []
            block_node = None
            for c in node.children[1:]:
                if isinstance(c, Tree) and c.data == "params":
                    params = [t.value for t in c.children]
                elif isinstance(c, Tree) and c.data == "block":
                    block_node = c
            func = Function(name_tok.value, params, block_node, env)
            env.set(name_tok.value, func)
            return None

        if node.data == "return_stmt":
            val = self.eval_expr(node.children[0], env)
            raise ReturnException(val)

        if node.data == "expr_stmt":
            self.eval_expr(node.children[0], env)
            return None

        # unknown
        return None

    def exec_block(self, block_node: Tree, env: Environment):
        # block_node.data == 'block' and children are statements
        for stmt in block_node.children:
            self.exec_stmt(stmt, env)

    def eval_expr(self, node, env: Environment):
        if isinstance(node, Token):
            if node.type == 'NUMBER':
                s = node.value
                if '.' in s or 'e' in s or 'E' in s:
                    return float(s)
                else:
                    return int(s)
            if node.type == 'STRING':
                return ast.literal_eval(node.value)
            if node.type == 'NAME':
                return env.get(node.value)
            return None

        if not isinstance(node, Tree):
            return None

        if node.data == 'number':
            tok = node.children[0]
            return self.eval_expr(tok, env)
        if node.data == 'string':
            tok = node.children[0]
            return self.eval_expr(tok, env)
        if node.data == 'var':
            name = node.children[0].value
            return env.get(name)

        if node.data in ('add', 'sub', 'mul', 'div'):
            left = self.eval_expr(node.children[0], env)
            right = self.eval_expr(node.children[1], env)
            if node.data == 'add':
                return left + right
            if node.data == 'sub':
                return left - right
            if node.data == 'mul':
                return left * right
            if node.data == 'div':
                return left / right

        if node.data == 'func_call':
            name_tok = node.children[0]
            args = []
            if len(node.children) > 1:
                args_node = node.children[1]
                for expr in args_node.children:
                    args.append(self.eval_expr(expr, env))
            name = name_tok.value
            # resolve function or builtin
            func_val = env.get(name)
            if callable(func_val) and not isinstance(func_val, Function):
                return func_val(*args)
            if isinstance(func_val, Function):
                return self.call_function(func_val, args)
            raise TypeError(f"'{name}' is not callable")

        # parentheses or other
        if node.data == 'expr':
            return self.eval_expr(node.children[0], env)

        raise NotImplementedError(f"Unsupported node: {node.data}")

    def call_function(self, func: Function, args):
        new_env = Environment(parent=func.env)
        for i, name in enumerate(func.params):
            val = args[i] if i < len(args) else None
            new_env.set(name, val)
        try:
            self.exec_block(func.body, new_env)
            return None
        except ReturnException as r:
            return r.value


def main(argv):
    interp = Interpreter()
    
    if len(argv) == 0:
        # No filename given, use default example file
        path = "examples/hello.chronos" 
         # Provide a default file name or path
    else:
        path = argv[0]

    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()

    interp.run(src)


if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
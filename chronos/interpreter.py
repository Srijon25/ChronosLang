from lark import Lark, Tree, Token
import sys
import ast
import argparse
import threading
import queue

chronos_grammar = r"""
start: stmt*

?stmt: var_assign
     | func_def
     | return_stmt
     | expr_stmt
     | go_stmt

// function with optional type annotations
func_def: "function" NAME "(" params? ")" return_type? ":" block
params: param ("," param)*
param: NAME (":" TYPE)?
return_type: "->" TYPE

block: "{" stmt* "}"

var_assign: NAME "=" expr
return_stmt: "return" expr
expr_stmt: expr

go_stmt: "go" expr

?expr: expr "+" term   -> add
     | expr "-" term   -> sub
     | term

?term: term "*" factor -> mul
     | term "/" factor -> div
     | factor

?factor: NUMBER        -> number
       | STRING        -> string
       | func_call
       | NAME          -> var
       | "(" expr ")"
       | send
       | recv
       | type_expr

func_call: NAME "(" args? ")"
args: expr ("," expr)*

send: NAME "<-" expr          // ch <- expr
recv: "<-" NAME               // <- ch
type_expr: "chan" TYPE       // chan int, chan float, etc.

%import common.CNAME -> NAME
%import common.SIGNED_NUMBER -> NUMBER
%import common.ESCAPED_STRING -> STRING

// TYPE is a terminal listing supported builtin types
TYPE: "int" | "float" | "string" | "auto"

// Ignore whitespace and comments
%ignore /[ \t\f\r\n]+/
%ignore /#[^\n]*/
"""


# Preprocessor (indent -> braces)

def preprocess_indent(src: str) -> str:
    """
    Convert Python-style indentation to explicit { ... } blocks.
    Robust against inline comments and blank lines.
    """
    lines = src.splitlines()
    out_lines = []
    indent_stack = [0]

    for i, raw in enumerate(lines):
        if raw.strip() == "":
            # skip empty lines (but do not produce '}'; keep neat)
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


class Channel:
    """
    Unbuffered rendezvous channel.
    - send(x) blocks until a receiver takes the value.
    - recv() blocks until a sender provides a value.
    This is a minimal single-slot rendezvous implementation (one waiting sender
    or one waiting receiver at a time). It's intentionally simple and deterministic
    for Week 3 demos.
    """

    def __init__(self, elem_type=None, buffer=0):
        # elem_type: optional string like 'int' or None (kept for typing info)
        # buffer ignored: this Channel is unbuffered (rendezvous).
        self.elem_type = elem_type

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # state for a waiting sender
        self._has_sender = False
        self._sender_value = None

        # state for a waiting receiver
        self._has_receiver = False
        self._receiver_value = None

        # helper flag so sender can wait for acknowledgement that receiver consumed
        self._sender_ack = False

    def send(self, value):
        with self._cond:
            # If a receiver is already waiting, transfer value and wake it.
            if self._has_receiver:
                # place value where receiver will read it
                self._receiver_value = value
                # mark receiver no longer waiting to accept; receiver will consume
                self._has_receiver = False
                # notify receiver
                self._cond.notify_all()

                # wait until receiver acknowledges that it consumed the value
                while not self._sender_ack:
                    self._cond.wait()
                # reset ack for future transfers
                self._sender_ack = False
                return

            # No receiver waiting -> become the sender and wait for a receiver
            # (only one waiting sender supported in this simple implementation)
            self._has_sender = True
            self._sender_value = value
            # wait until a receiver pairs with us and takes the value
            while self._has_sender:
                self._cond.wait()
            # at this point receiver has consumed the value and returned,
            # so send() can complete.
            return

    def recv(self):
        with self._cond:
            # If a sender is already waiting, take its value immediately
            if self._has_sender:
                val = self._sender_value
                # mark sender as consumed
                self._has_sender = False
                self._sender_value = None
                # notify sender so it can continue
                self._cond.notify_all()
                return val

            # No sender waiting -> become a receiver and wait for a sender to provide value
            self._has_receiver = True
            # wait until a sender transfers a value into _receiver_value
            while self._has_receiver and self._receiver_value is None:
                self._cond.wait()

            # receiver_value was set by send()
            val = self._receiver_value
            self._receiver_value = None
            # acknowledge to sender that we consumed the value (unblocks send())
            self._sender_ack = True
            # notify sender that ack was set
            self._cond.notify_all()
            return val




# Helpers for AST tokens/names

def get_name(node):
    """
    Return the string for a NAME/TYPE whether node is Token or Tree wrapper.
    """
    if isinstance(node, Token):
        return node.value
    if isinstance(node, Tree):
        # search for NAME/TYPE token among children
        for c in node.children:
            if isinstance(c, Token) and c.type in ("NAME", "TYPE"):
                return c.value
            if isinstance(c, Tree):
                v = get_name(c)
                if v:
                    return v
    return None


def extract_name_tokens(node):
    """
    Return a list of NAME/TYPE token string values found in node (recursively), in order.
    """
    out = []
    if isinstance(node, Token):
        if node.type in ("NAME", "TYPE"):
            out.append(node.value)
        return out
    if isinstance(node, Tree):
        for c in node.children:
            out.extend(extract_name_tokens(c))
    return out


def unify_numeric(t1, t2):
    """Return unified numeric type or None if incompatible."""
    numeric = {"int", "float"}
    if t1 in numeric and t2 in numeric:
        if t1 == "float" or t2 == "float":
            return "float"
        return "int"
    return None


def is_compatible(expected, actual):
    """Return True if actual can be used where expected is required."""
    if expected == "auto" or expected is None:
        return True
    if expected == actual:
        return True
    if expected == "float" and actual == "int":
        return True
    return False



# Runtime structures

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
    def __init__(self, name, params, param_types, return_type, body, env):
        # params: list of param names
        # param_types: list of type strings ('int','float','string','auto')
        self.name = name
        self.params = params
        self.param_types = param_types
        self.return_type = return_type
        self.body = body  # block Tree
        self.env = env


class ReturnException(Exception):
    def __init__(self, value):
        self.value = value



# TypeChecker (static) - extended for go/send/recv/type_expr
class TypeChecker:
    def __init__(self, tree: Tree, infer: bool = False):
        self.tree = tree
        self.infer = infer
        self.functions = {}
        self.builtins = {"print": {"param_types": ["auto"], "return_type": "void"},
                         "make": {"param_types": ["auto"], "return_type": "chan"}}

    def check(self):
        for node in self.tree.children:
            if isinstance(node, Tree) and node.data == "func_def":
                self._collect_signature(node)

        for node in self.tree.children:
            if not (isinstance(node, Tree) and node.data == "func_def"):
                self._check_stmt(node, {}, [])

        for name, sig in list(self.functions.items()):
            if not sig.get("checked"):
                self._check_function(sig)

    def _collect_signature(self, node: Tree):
        name_tok = node.children[0]
        name = get_name(name_tok)
        params = []
        param_types = []
        return_type = "auto"
        block_node = None

        for c in node.children[1:]:
            if isinstance(c, Tree) and c.data == "params":
                for p in c.children:
                    names = extract_name_tokens(p)
                    if len(names) >= 1:
                        params.append(names[0])
                        ptype = names[1] if len(names) > 1 else "auto"
                        param_types.append(ptype)
            elif isinstance(c, Tree) and c.data == "return_type":
                tnames = extract_name_tokens(c)
                if tnames:
                    return_type = tnames[-1]
            elif isinstance(c, Tree) and c.data == "block":
                block_node = c

        while len(param_types) < len(params):
            param_types.append("auto")

        self.functions[name] = {
            "name": name,
            "params": params,
            "param_types": param_types,
            "return_type_decl": return_type,
            "node": node,
            "block": block_node,
            "inferred_return": None,
            "checked": False,
        }

    def _check_function(self, sig):
        if sig.get("checked"):
            return
        local_types = {}
        for i, pname in enumerate(sig["params"]):
            ptype = sig["param_types"][i] if i < len(sig["param_types"]) else "auto"
            local_types[pname] = ptype

        returns = []
        block = sig["block"]
        if block is None:
            sig["inferred_return"] = "void"
            sig["checked"] = True
            return

        for stmt in block.children:
            self._check_stmt(stmt, local_types, returns)

        declared = sig.get("return_type_decl") or "auto"
        if returns:
            unified = returns[0]
            for r in returns[1:]:
                if unified == r:
                    continue
                un = unify_numeric(unified, r)
                if un:
                    unified = un
                    continue
                if unified == "auto" and r != "auto":
                    unified = r
                    continue
                if r == "auto" and unified != "auto":
                    continue
                raise TypeError(f"Conflicting return types in function '{sig['name']}': {unified} vs {r}")

            if declared != "auto" and not is_compatible(declared, unified):
                raise TypeError(f"Function '{sig['name']}' declared return {declared} but returns {unified}")
            sig["inferred_return"] = unified
        else:
            sig["inferred_return"] = "void"

        sig["checked"] = True

    def _check_stmt(self, stmt, local_types, returns):
        if isinstance(stmt, Token):
            return
        if stmt.data == "var_assign":
            name_tok = stmt.children[0]
            expr = stmt.children[1]
            et = self._expr_type(expr, local_types)
            name = get_name(name_tok)
            if name in local_types:
                old = local_types[name]
                if old != "auto" and not is_compatible(old, et):
                    raise TypeError(f"Assignment type mismatch for '{name}': {old} vs {et}")
            local_types[name] = et
            return
        if stmt.data == "return_stmt":
            et = self._expr_type(stmt.children[0], local_types)
            returns.append(et)
            return
        if stmt.data == "expr_stmt":
            self._expr_type(stmt.children[0], local_types)
            return
        if stmt.data == "go_stmt":
            # type-check expression launched in background
            self._expr_type(stmt.children[0], local_types)
            return

    def _expr_type(self, node, local_types):
        # Token leaf
        if isinstance(node, Token):
            if node.type == 'NUMBER':
                s = node.value
                if '.' in s or 'e' in s or 'E' in s:
                    return "float"
                return "int"
            if node.type == 'STRING':
                return "string"
            if node.type == 'NAME':
                return local_types.get(node.value, "unknown")
            return "unknown"

        if not isinstance(node, Tree):
            return "unknown"

        if node.data == 'number':
            return self._expr_type(node.children[0], local_types)
        if node.data == 'string':
            return self._expr_type(node.children[0], local_types)
        if node.data == 'var':
            name = get_name(node.children[0])
            return local_types.get(name, "unknown")

        if node.data in ('add', 'sub', 'mul', 'div'):
            left = self._expr_type(node.children[0], local_types)
            right = self._expr_type(node.children[1], local_types)
            if left in {"int", "float"} and right in {"int", "float"}:
                return "float" if ("float" in (left, right)) else "int"
            if left == "auto" or right == "auto" or left == "unknown" or right == "unknown":
                if node.data == 'add' and (left == "string" or right == "string"):
                    return "string"
                return "auto"
            if node.data == 'add' and left == "string" and right == "string":
                return "string"
            raise TypeError(f"Type error in arithmetic: {left} {node.data} {right}")

        if node.data == 'func_call':
            name_tok = node.children[0]
            args_types = []
            if len(node.children) > 1:
                args_node = node.children[1]
                for expr in args_node.children:
                    args_types.append(self._expr_type(expr, local_types))
            fname = get_name(name_tok)

            # builtin
            if fname in self.builtins:
                return self.builtins[fname]["return_type"]

            if fname not in self.functions:
                raise NameError(f"Call to unknown function '{fname}'")
            fsig = self.functions[fname]

            if len(args_types) != len(fsig["params"]):
                raise TypeError(f"Function '{fname}' expects {len(fsig['params'])} args, got {len(args_types)}")

                        # If inference is disabled, only statically check where parameter type is concrete (not 'auto')
            if not self.infer:
                for i, actual in enumerate(args_types):
                    expected = fsig["param_types"][i] if i < len(fsig["param_types"]) else "auto"
                    if expected != "auto" and actual not in ("auto", "unknown") and not is_compatible(expected, actual):
                        raise TypeError(f"In call to '{fname}': argument {i+1} expected {expected}, got {actual}")
            else:
                # inference-enabled: attempt to infer auto param types and re-check function body
                changed = False
                new_param_types = list(fsig["param_types"])
                for i, actual in enumerate(args_types):
                    expected = new_param_types[i] if i < len(new_param_types) else "auto"
                    if expected == "auto" and actual not in ("auto", "unknown"):
                        new_param_types[i] = actual
                        changed = True
                    else:
                        if expected != "auto" and actual not in ("auto", "unknown") and not is_compatible(expected, actual):
                            raise TypeError(f"In call to '{fname}': argument {i+1} expected {expected}, got {actual}")
                if changed:
                    fsig["param_types"] = new_param_types
                    self._check_function(fsig)
                for i, actual in enumerate(args_types):
                    expected = fsig["param_types"][i] if i < len(fsig["param_types"]) else "auto"
                    if not is_compatible(expected, actual):
                        raise TypeError(f"In call to '{fname}': argument {i+1} expected {expected}, got {actual}")

            # determine return type
            if fsig.get("return_type_decl") and fsig["return_type_decl"] != "auto":
                return fsig["return_type_decl"]
            if fsig.get("inferred_return"):
                return fsig["inferred_return"]
            return "auto"

        if node.data == 'type_expr':
            names = extract_name_tokens(node)
            if len(names) >= 2 and names[0] == 'chan':
                return f"chan<{names[1]}>"
            return "chan"

        if node.data == 'send':
            # send: NAME "<-" expr
            # ensure channel exists and types roughly match (best-effort)
            ch_name = get_name(node.children[0])
            val_type = self._expr_type(node.children[1], local_types)
            return "void"

        if node.data == 'recv':
            # recv: "<-" NAME
            ch_name = get_name(node.children[0])
            return "unknown"

        if node.data == 'expr':
            return self._expr_type(node.children[0], local_types)

        raise NotImplementedError(f"TypeChecker: unsupported node {node.data}")


# Interpreter (runtime), extended with concurrency primitives
class Interpreter:
    def __init__(self):
        self.parser = Lark(chronos_grammar, parser="lalr", propagate_positions=True)
        self.global_env = Environment()
        # builtin: print
        self.global_env.set("print", lambda *args: print(*args))
        self.type_info = {}

    def run(self, src: str, skip_typecheck: bool = False, permissive: bool = False, infer: bool = False):

        src2 = preprocess_indent(src)
        tree = self.parser.parse(src2)
        # run static checker
        if not skip_typecheck:
            checker = TypeChecker(tree, infer=infer)
            try:
                checker.check()
                if infer:
                    self.type_info = checker.functions
            except TypeError as e:
                if permissive:
                    print(f"TypeWarning (static): {e}  -- continuing execution (permissive mode).")
                    try:
                        self.type_info = checker.functions
                    except Exception:
                        self.type_info = {}
                else:
                    raise

        # execute program (propagate permissive flag)
        self.exec_tree(tree, self.global_env, permissive)

    def exec_tree(self, tree: Tree, env: Environment, permissive: bool):
        for node in tree.children:
            self.exec_stmt(node, env, permissive)

    def exec_stmt(self, node, env: Environment, permissive: bool):
        if isinstance(node, Token):
            return
        if node.data == "var_assign":
            name_tok = node.children[0]
            expr = node.children[1]
            value = self.eval_expr(expr, env, permissive)
            env.set(get_name(name_tok), value)
            return None

        if node.data == "func_def":
            # children: NAME, params? , return_type? , block
            name_tok = node.children[0]
            params = []
            param_types = []
            return_type = "auto"
            block_node = None
            for c in node.children[1:]:
                if isinstance(c, Tree) and c.data == "params":
                    for p in c.children:
                        names = extract_name_tokens(p)
                        if names:
                            params.append(names[0])
                            ptype = names[1] if len(names) > 1 else "auto"
                            param_types.append(ptype)
                elif isinstance(c, Tree) and c.data == "return_type":
                    rtokens = extract_name_tokens(c)
                    if rtokens:
                        return_type = rtokens[-1]
                elif isinstance(c, Tree) and c.data == "block":
                    block_node = c

            fname = get_name(name_tok)

            # prefer inferred param types from type_info if available
            if fname in self.type_info:
                sig = self.type_info[fname]
                if sig.get("param_types"):
                    param_types = list(sig["param_types"])[:len(params)]
                if sig.get("return_type_decl") and sig["return_type_decl"] != "auto":
                    return_type = sig["return_type_decl"]
                elif sig.get("inferred_return"):
                    return_type = sig["inferred_return"]

            while len(param_types) < len(params):
                param_types.append("auto")

            func = Function(fname, params, param_types, return_type, block_node, env)
            env.set(fname, func)
            return None

        if node.data == "go_stmt":
            expr = node.children[0]
            # If expr is a function call, prepare args but don't execute here
            if isinstance(expr, Tree) and expr.data == 'func_call':
                name_tok = expr.children[0]
                args_nodes = expr.children[1] if len(expr.children) > 1 else None
                args_vals = []
                if args_nodes:
                    for e in args_nodes.children:
                        args_vals.append(self.eval_expr(e, env, permissive))
                func_val = env.get(get_name(name_tok))
                if not isinstance(func_val, Function):
                    raise TypeError(f"'go' applied to non-function '{get_name(name_tok)}'")

                def target(fv=func_val, av=args_vals):
                    try:
                        self.call_function(fv, av, permissive)
                    except Exception as ex:
                        print("[background thread error]", ex)

                t = threading.Thread(target=target, daemon=True)
                t.start()
                return None
            else:
                # generic expression in new thread
                def target_expr(e=expr, env_local=env):
                    try:
                        self.eval_expr(e, env_local, permissive)
                    except Exception as ex:
                        print("[background thread error]", ex)
                t = threading.Thread(target=target_expr, daemon=True)
                t.start()
                return None

        if node.data == "return_stmt":
            val = self.eval_expr(node.children[0], env, permissive)
            raise ReturnException(val)

        if node.data == "expr_stmt":
            self.eval_expr(node.children[0], env, permissive)
            return None

        return None

    def exec_block(self, block_node: Tree, env: Environment, permissive: bool):
        for stmt in block_node.children:
            self.exec_stmt(stmt, env, permissive)

    def eval_expr(self, node, env: Environment, permissive: bool):
        # Token leaf
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
            return self.eval_expr(tok, env, permissive)
        if node.data == 'string':
            tok = node.children[0]
            return self.eval_expr(tok, env, permissive)
        if node.data == 'var':
            name = get_name(node.children[0])
            return env.get(name)

        if node.data in ('add', 'sub', 'mul', 'div'):
            left = self.eval_expr(node.children[0], env, permissive)
            right = self.eval_expr(node.children[1], env, permissive)

            def rtype(v):
                if isinstance(v, int):
                    return "int"
                if isinstance(v, float):
                    return "float"
                if isinstance(v, str):
                    return "string"
                return "unknown"

            lt = rtype(left)
            rt = rtype(right)

            if node.data == 'add':
                # numeric + numeric
                if lt in ("int", "float") and rt in ("int", "float"):
                    return left + right
                # string + string
                if lt == "string" and rt == "string":
                    return left + right
                # mixed: permissive -> coerce to string concatenation
                if permissive:
                    return str(left) + str(right)
                raise TypeError(f"Runtime type error in '+': {lt} + {rt}")

            # other binary ops require numeric
            if lt in ("int", "float") and rt in ("int", "float"):
                if node.data == 'sub':
                    return left - right
                if node.data == 'mul':
                    return left * right
                if node.data == 'div':
                    return left / right
            if permissive:
                # coerce to float if possible
                try:
                    l = float(left)
                    r = float(right)
                    if node.data == 'sub':
                        return l - r
                    if node.data == 'mul':
                        return l * r
                    if node.data == 'div':
                        return l / r
                except Exception:
                    pass
                raise TypeError(f"Runtime type error (permissive failed) in '{node.data}': {lt} {node.data} {rt}")
            raise TypeError(f"Runtime type error in '{node.data}': {lt} {node.data} {rt}")

        if node.data == 'func_call':
            name_tok = node.children[0]
            args = []
            if len(node.children) > 1:
                args_node = node.children[1]
                for expr in args_node.children:
                    args.append(self.eval_expr(expr, env, permissive))
            fname = get_name(name_tok)
            # special-case 'make' builtin expecting a type_expr sentinel
            if fname == 'make':
                if args and isinstance(args[0], tuple) and args[0][0] == 'chan':
                    # args[0] is ('chan', 'int') or ('chan', None)
                    elem = args[0][1]
                    return Channel(elem_type=elem)
                return Channel()

            try:
                func_val = env.get(fname)
            except NameError:
                raise NameError(f"Function '{fname}' is not defined")
            if callable(func_val) and not isinstance(func_val, Function):
                return func_val(*args)
            if isinstance(func_val, Function):
                return self.call_function(func_val, args, permissive)
            raise TypeError(f"'{fname}' is not callable")

        if node.data == 'type_expr':
            names = extract_name_tokens(node)
            if len(names) >= 2 and names[0] == 'chan':
                return ('chan', names[1])
            return ('chan', None)

        if node.data == 'send':
            # send: NAME "<-" expr
            name_tok = node.children[0]
            val = self.eval_expr(node.children[1], env, permissive)
            ch = env.get(get_name(name_tok))
            if not isinstance(ch, Channel):
                raise TypeError(f"'{get_name(name_tok)}' is not a channel")
            ch.send(val)
            return None

        if node.data == 'recv':
            # recv: "<-" NAME
            name_tok = node.children[0]
            ch = env.get(get_name(name_tok))
            if not isinstance(ch, Channel):
                raise TypeError(f"'{get_name(name_tok)}' is not a channel")
            return ch.recv()

        if node.data == 'expr':
            return self.eval_expr(node.children[0], env, permissive)

        raise NotImplementedError(f"Unsupported node: {node.data}")

    def call_function(self, func: Function, args, permissive: bool):
        # runtime argument type checks (respect permissive)
        for i, expected in enumerate(func.param_types):
            actual_val = args[i] if i < len(args) else None
            if actual_val is None:
                actual_type = "unknown"
            elif isinstance(actual_val, int):
                actual_type = "int"
            elif isinstance(actual_val, float):
                actual_type = "float"
            elif isinstance(actual_val, str):
                actual_type = "string"
            elif isinstance(actual_val, Channel):
                actual_type = f"chan<{actual_val.elem_type}>" if actual_val.elem_type else "chan"
            else:
                actual_type = "unknown"

            if not is_compatible(expected, actual_type):
                if permissive:
                    # attempt some safe coercions in permissive mode
                    if expected == "string":
                        args[i] = str(actual_val)
                    elif expected == "float" and actual_type == "int":
                        args[i] = float(actual_val)
                    else:
                        # warn but continue (no coercion available)
                        print(f"RuntimeWarning: Function '{func.name}': argument {i+1} expected {expected}, got {actual_type} -- continuing (permissive)")
                else:
                    raise TypeError(f"Function '{func.name}': argument {i+1} expected {expected}, got {actual_type}")

        # prepare new env for function
        new_env = Environment(parent=func.env)
        for i, name in enumerate(func.params):
            val = args[i] if i < len(args) else None
            new_env.set(name, val)
        try:
            if func.body:
                self.exec_block(func.body, new_env, permissive)
            return None
        except ReturnException as r:
            return r.value



# Main CLI

def main(argv):
    ap = argparse.ArgumentParser(prog="chronos/interpreter.py")
    ap.add_argument("--infer", action="store_true", help="Enable inference of 'auto' param types from call-sites")
    ap.add_argument("path", nargs="?", default="examples/hello.chronos", help="source file")
    ap.add_argument("--permissive", action="store_true", help="Treat static type errors as warnings and coerce at runtime")
    ap.add_argument("--skip-typecheck", action="store_true", help="Skip static type checking")
    args = ap.parse_args(argv)

    with open(args.path, "r", encoding="utf-8") as f:
        src = f.read()

    interp = Interpreter()
    interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive)


if __name__ == "__main__":
    main(sys.argv[1:])
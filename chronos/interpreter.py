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
     | test_def
     | assert_stmt

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



# Channel (unbuffered rendezvous)

class Channel:
    """
    Simple unbuffered rendezvous channel:
    - send(x) blocks until a receiver takes the value.
    - recv() blocks until a sender provides a value.
    This single-slot design is deterministic for demos.
    """
    def __init__(self, elem_type=None, buffer=0):
        self.elem_type = elem_type
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._has_sender = False
        self._sender_value = None
        self._has_receiver = False
        self._receiver_value = None
        self._sender_ack = False

    def send(self, value):
        with self._cond:
            # if receiver waiting, transfer immediately
            if self._has_receiver:
                self._receiver_value = value
                self._has_receiver = False
                self._cond.notify_all()
                # wait for receiver to ack consumption
                while not self._sender_ack:
                    self._cond.wait()
                self._sender_ack = False
                return
            # no receiver: become waiting sender
            self._has_sender = True
            self._sender_value = value
            while self._has_sender:
                self._cond.wait()
            return

    def recv(self):
        with self._cond:
            # if sender waiting, take it immediately
            if self._has_sender:
                val = self._sender_value
                self._has_sender = False
                self._sender_value = None
                self._cond.notify_all()
                return val
            # no sender: become waiting receiver
            self._has_receiver = True
            while self._has_receiver and self._receiver_value is None:
                self._cond.wait()
            val = self._receiver_value
            self._receiver_value = None
            # ack to sender
            self._sender_ack = True
            self._cond.notify_all()
            return val



# Helpers (AST tokens/names)

def get_name(node):
    """Return NAME/TYPE token string from Token or Tree wrapper."""
    if isinstance(node, Token):
        return node.value
    if isinstance(node, Tree):
        for c in node.children:
            if isinstance(c, Token) and c.type in ("NAME", "TYPE"):
                return c.value
            if isinstance(c, Tree):
                v = get_name(c)
                if v:
                    return v
    return None


def extract_name_tokens(node):
    """Return list of NAME/TYPE token string values found in node."""
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
    numeric = {"int", "float"}
    if t1 in numeric and t2 in numeric:
        if t1 == "float" or t2 == "float":
            return "float"
        return "int"
    return None


def is_compatible(expected, actual):
    if expected == "auto" or expected is None:
        return True
    if expected == actual:
        return True
    if expected == "float" and actual == "int":
        return True
    if isinstance(expected, str) and expected.startswith("chan") and isinstance(actual, str) and actual.startswith("chan"):
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
        self.name = name
        self.params = params
        self.param_types = param_types
        self.return_type = return_type
        self.body = body
        self.env = env


class ReturnException(Exception):
    def __init__(self, value):
        self.value = value



# TypeChecker (conservative)

class TypeChecker:
    """
    Conservative static type checker. Extended to tolerate test/assert/send/recv/type_expr/go.
    """
    def __init__(self, tree: Tree, infer: bool = False):
        self.tree = tree
        self.infer = infer
        self.functions = {}
        self.builtins = {"print": {"param_types": ["auto"], "return_type": "void"},
                         "make": {"param_types": ["auto"], "return_type": "chan"}}

    def check(self):
        # collect functions
        for node in self.tree.children:
            if isinstance(node, Tree) and node.data == "func_def":
                self._collect_signature(node)

        # check top-level (non func_def) nodes (includes test defs)
        for node in self.tree.children:
            if not (isinstance(node, Tree) and node.data == "func_def"):
                self._check_stmt(node, {}, [])

        # ensure remaining functions checked
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
            self._expr_type(stmt.children[0], local_types)
            return
        if stmt.data == "test_def":
            # check test body statements conservatively
            block = stmt.children[1]
            for s in block.children:
                self._check_stmt(s, local_types.copy(), [])
            return
        if stmt.data == "assert_stmt":
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

        if node.data in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
            # comparisons -> boolean-ish; return 'auto' (conservative)
            # but still check subexpr types for basic errors
            self._expr_type(node.children[0], local_types)
            self._expr_type(node.children[1], local_types)
            return "auto"

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

            if not self.infer:
                for i, actual in enumerate(args_types):
                    expected = fsig["param_types"][i] if i < len(fsig["param_types"]) else "auto"
                    if expected != "auto" and actual not in ("auto", "unknown") and not is_compatible(expected, actual):
                        raise TypeError(f"In call to '{fname}': argument {i+1} expected {expected}, got {actual}")
            else:
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
            # best-effort: check element type of value
            self._expr_type(node.children[1], local_types)
            return "void"

        if node.data == 'recv':
            return "unknown"

        if node.data == 'expr':
            return self._expr_type(node.children[0], local_types)

        raise NotImplementedError(f"TypeChecker: unsupported node {node.data}")



# Interpreter (runtime)

class Interpreter:
    def __init__(self):
        self.parser = Lark(chronos_grammar, parser="lalr", propagate_positions=True)
        self.global_env = Environment()
        # builtins
        self.global_env.set("print", lambda *args: print(*args))
        # expose a trivial sleep for demos (optional)
        try:
            import time
            self.global_env.set("sleep", lambda s: time.sleep(s))
        except Exception:
            pass
        self.type_info = {}

    def run(self, src: str, skip_typecheck: bool = False, permissive: bool = False,
            infer: bool = False, execute: bool = True, run_tests: bool = False):
        """
        Parse, optionally type-check, optionally execute, optionally run tests.
        - execute=False: only type-check (used by `build`).
        - run_tests=True: load module then run tests collected in file.
        """
        src2 = preprocess_indent(src)
        tree = self.parser.parse(src2)

        # static check
        if not skip_typecheck:
            checker = TypeChecker(tree, infer=infer)
            try:
                checker.check()
                if infer:
                    self.type_info = checker.functions
            except TypeError as e:
                if permissive:
                    print(f"TypeWarning (static): {e} -- continuing (permissive).")
                    try:
                        self.type_info = checker.functions
                    except Exception:
                        self.type_info = {}
                else:
                    raise

        # partition nodes: non-test vs test
        prelude_nodes = []
        test_nodes = []
        for node in tree.children:
            if isinstance(node, Tree) and node.data == "test_def":
                test_nodes.append(node)
            else:
                prelude_nodes.append(node)

        # prepare module environment and run prelude if execution requested
        module_env = Environment()
        # copy builtins into module_env
        module_env.vars.update(self.global_env.vars)

        if execute or run_tests:
            # execute all prelude nodes (this sets up functions/variables/state)
            for node in prelude_nodes:
                # skip top-level test_def (already separated)
                self.exec_stmt(node, module_env, permissive)

        if run_tests:
            return self._run_tests(test_nodes, module_env, permissive)

        if execute:
            return None  # normal run executed during prelude loop above

        # nothing else to do (build-only)
        return None

    def _run_tests(self, test_nodes, module_env: Environment, permissive: bool):
        results = []
        for node in test_nodes:
            # node.children: [STRING, block]
            name_tok = node.children[0]
            block_node = node.children[1]
            try:
                test_name = ast.literal_eval(name_tok.value) if isinstance(name_tok, Token) and name_tok.type == "STRING" else str(get_name(name_tok))
            except Exception:
                test_name = str(get_name(name_tok))

            # run each test in a fresh child environment so tests are isolated
            test_env = Environment(parent=module_env)
            try:
                self.exec_block(block_node, test_env, permissive)
                results.append((test_name, "PASS", None))
                print(f"[PASS] {test_name}")
            except AssertionError as ae:
                msg = str(ae) if ae.args else ""
                results.append((test_name, "FAIL", msg))
                print(f"[FAIL] {test_name}  -- assertion failed: {msg}")
            except Exception as ex:
                results.append((test_name, "ERROR", str(ex)))
                print(f"[ERROR] {test_name}  -- exception: {ex}")

        # summary
        passed = sum(1 for r in results if r[1] == "PASS")
        failed = sum(1 for r in results if r[1] == "FAIL")
        errored = sum(1 for r in results if r[1] == "ERROR")
        total = len(results)
        print("")
        print(f"Test results: {passed}/{total} passed, {failed} failed, {errored} errors")

        # return non-zero exit condition if any failure/error
        ok = (failed == 0 and errored == 0)
        return ok

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
                def target_expr(e=expr, env_local=env):
                    try:
                        self.eval_expr(e, env_local, permissive)
                    except Exception as ex:
                        print("[background thread error]", ex)
                t = threading.Thread(target=target_expr, daemon=True)
                t.start()
                return None

        if node.data == "test_def":
            # top-level test definitions are handled in run() : ignore on normal exec
            return None

        if node.data == "assert_stmt":
            cond = self.eval_expr(node.children[0], env, permissive)
            if not cond:
                raise AssertionError("assertion failed")
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
                if lt in ("int", "float") and rt in ("int", "float"):
                    return left + right
                if lt == "string" and rt == "string":
                    return left + right
                if permissive:
                    return str(left) + str(right)
                raise TypeError(f"Runtime type error in '+': {lt} + {rt}")

            if lt in ("int", "float") and rt in ("int", "float"):
                if node.data == 'sub':
                    return left - right
                if node.data == 'mul':
                    return left * right
                if node.data == 'div':
                    return left / right
            if permissive:
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

        if node.data in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
            left = self.eval_expr(node.children[0], env, permissive)
            right = self.eval_expr(node.children[1], env, permissive)
            if node.data == 'eq':
                return left == right
            if node.data == 'ne':
                return left != right
            if node.data == 'lt':
                return left < right
            if node.data == 'le':
                return left <= right
            if node.data == 'gt':
                return left > right
            if node.data == 'ge':
                return left >= right

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
            name_tok = node.children[0]
            val = self.eval_expr(node.children[1], env, permissive)
            ch = env.get(get_name(name_tok))
            if not isinstance(ch, Channel):
                raise TypeError(f"'{get_name(name_tok)}' is not a channel")
            ch.send(val)
            return None

        if node.data == 'recv':
            name_tok = node.children[0]
            ch = env.get(get_name(name_tok))
            if not isinstance(ch, Channel):
                raise TypeError(f"'{get_name(name_tok)}' is not a channel")
            return ch.recv()

        if node.data == 'expr':
            return self.eval_expr(node.children[0], env, permissive)

        raise NotImplementedError(f"Unsupported node: {node.data}")

    def call_function(self, func: Function, args, permissive: bool):
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
                    if expected == "string":
                        args[i] = str(actual_val)
                    elif expected == "float" and actual_type == "int":
                        args[i] = float(actual_val)
                    else:
                        print(f"RuntimeWarning: Function '{func.name}': argument {i+1} expected {expected}, got {actual_type} -- continuing (permissive)")
                else:
                    raise TypeError(f"Function '{func.name}': argument {i+1} expected {expected}, got {actual_type}")

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



# CLI: build / run / test

def main(argv):
    # legacy convenience: if first arg looks like a path (not a subcommand or a flag),
    # treat it as: run <path>
    if len(argv) > 0 and not argv[0].startswith("-") and argv[0] not in ("run", "test", "build"):
        argv = ["run"] + argv

    ap = argparse.ArgumentParser(prog="chronos/interpreter.py")
    sub = ap.add_subparsers(dest="cmd", required=False)

    # run
    p_run = sub.add_parser("run", help="Run a ChronosLang source file")
    p_run.add_argument("path", nargs="?", default="examples/hello.chronos")
    p_run.add_argument("--skip-typecheck", action="store_true")
    p_run.add_argument("--permissive", action="store_true")
    p_run.add_argument("--infer", action="store_true")

    # test
    p_test = sub.add_parser("test", help="Run tests in a ChronosLang source file")
    p_test.add_argument("path", nargs="?", default="examples")
    p_test.add_argument("--skip-typecheck", action="store_true")
    p_test.add_argument("--permissive", action="store_true")
    p_test.add_argument("--infer", action="store_true")

    # build (skeleton)
    p_build = sub.add_parser("build", help="Build/package skeleton (type-check only)")
    p_build.add_argument("path", nargs="?", default=".")
    p_build.add_argument("--infer", action="store_true")

    # legacy single-file invocation (keeps compatibility)
    ap.add_argument("--infer", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--permissive", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--skip-typecheck", action="store_true", help=argparse.SUPPRESS)
    # IMPORTANT: use a different dest name so we don't shadow subparser 'path'
    ap.add_argument("legacy_path", nargs="?", default=None, help=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    interp = Interpreter()

    # Helper to resolve a path value safely (prefers explicit subparser 'path', then legacy_path)
    def resolved_path(parsed_args):
        p = getattr(parsed_args, "path", None)
        if p is not None:
            return p
        return getattr(parsed_args, "legacy_path", None)

    if args.cmd == "run":
        path_to_open = resolved_path(args) or "examples/hello.chronos"
        with open(path_to_open, "r", encoding="utf-8") as f:
            src = f.read()
        interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive, infer=args.infer, execute=True, run_tests=False)
        return

    if args.cmd == "test":
        # Run tests: if path is directory run every .chronos file; otherwise single file
        import os
        path = resolved_path(args) or "examples"
        files = []
        if os.path.isdir(path):
            for fname in sorted(os.listdir(path)):
                if fname.endswith(".chronos"):
                    files.append(os.path.join(path, fname))
        else:
            files.append(path)
        overall_ok = True
        for fpath in files:
            print(f"Running tests in {fpath}")
            with open(fpath, "r", encoding="utf-8") as f:
                src = f.read()
            ok = interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive, infer=args.infer, execute=True, run_tests=True)
            if not ok:
                overall_ok = False
        if not overall_ok:
            sys.exit(1)
        return

    if args.cmd == "build":
        # Simple build: find a main file or typecheck given path
        import os
        path = resolved_path(args) or "."
        main_file = None
        if os.path.isdir(path):
            # look for src/main.chronos or main.chronos
            if os.path.exists(os.path.join(path, "src", "main.chronos")):
                main_file = os.path.join(path, "src", "main.chronos")
            elif os.path.exists(os.path.join(path, "main.chronos")):
                main_file = os.path.join(path, "main.chronos")
        else:
            main_file = path
        if not main_file or not os.path.exists(main_file):
            print("Build: no main.chronos found in directory. Please specify a file.")
            sys.exit(2)
        with open(main_file, "r", encoding="utf-8") as f:
            src = f.read()
        # run type-check only (execute=False)
        try:
            interp.run(src, skip_typecheck=False, permissive=False, infer=args.infer, execute=False, run_tests=False)
            print("Build: type-check OK")
        except Exception as e:
            print("Build failed:", e)
            sys.exit(1)
        return

    # backward compatible single-file invocation: behave as 'run'
    # read path from legacy_path or default if not provided
    path = resolved_path(args) or "examples/hello.chronos"
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive, infer=args.infer, execute=True, run_tests=False)

if __name__ == '__main__':
    main(sys.argv[1:])

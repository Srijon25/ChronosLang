from lark import Lark, Tree, Token
import sys
import ast
import argparse
import threading
import queue
import bisect
import math
import time as _time
import numpy as _np
import math as _math

# Optional PyTorch for acceleration/autodiff
try:
    import torch as _torch
except Exception:
    _torch = None

chronos_grammar = r"""
start: stmt*

?stmt: var_assign
     | temporal_decl
     | func_def
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


# Helper Classes / Functions

class ListType:
    def __init__(self, elem_type=None):
        self.elem_type = elem_type

    def __repr__(self):
        return f"List[{self.elem_type}]"

def get_name(tok):
    return tok.value if isinstance(tok, Token) else str(tok)

def extract_name_tokens(node):
    # Returns list of names from a tree node (for params, types)
    if isinstance(node, Tree):
        return [get_name(c) for c in node.children if isinstance(c, Token)]
    return []

def is_compatible(expected, actual):
    # Conservative compatibility
    if expected == "auto" or actual == "auto" or expected == actual:
        return True
    if expected == "float" and actual == "int":
        return True
    return False


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


# Temporal variable and timeline support
class TemporalVar:
    """
    Holds a timeline of (time, value) pairs (time: float seconds).
    value_at(t) returns the most recent value whose time <= t or None.
    set_at(t, value) appends or inserts in sorted history.
    """
    def __init__(self, name, initial_value, timeline):
        self.name = name
        self.timeline = timeline  # reference to Timeline
        # store history as two parallel lists for efficient bisect
        self.times = [timeline.current_time]
        self.values = [initial_value]

    def set_at(self, t: float, value):
        # insert in sorted order (replace if same time)
        i = bisect.bisect_left(self.times, t)
        if i < len(self.times) and math.isclose(self.times[i], t):
            self.values[i] = value
        else:
            self.times.insert(i, t)
            self.values.insert(i, value)

    def value_at(self, t: float):
        # return last value with time <= t
        i = bisect.bisect_right(self.times, t) - 1
        if i >= 0:
            return self.values[i]
        return None

    def history(self):
        return list(zip(self.times, self.values))


class Timeline:
    """
    Collect scheduled assignments and advance a logical current_time.
    scheduled events stored by time.
    """
    def __init__(self):
        self.current_time = 0.0
        self._events = {}  # map float -> list of (name, value)
        self._sorted_times = []

    def schedule(self, at_time: float, name: str, value):
        at_time = float(at_time)
        lst = self._events.get(at_time)
        if lst is None:
            self._events[at_time] = [(name, value)]
            bisect.insort(self._sorted_times, at_time)
        else:
            lst.append((name, value))

    def times(self):
        return list(self._sorted_times)

    def run_to(self, t: float, env: 'Environment'):
        """
        Advance current_time forward (or backward) and apply events up to new time.
        When moving forward, apply events for times <= t (in order).
        When moving backward, do nothing to applied history (history query will read older values).
        """
        if t < self.current_time:
            # stepping backward — don't remove history; just move pointer
            self.current_time = t
            return

        # apply events for times > current_time and <= t
        i = 0
        while i < len(self._sorted_times) and self._sorted_times[i] <= t:
            event_time = self._sorted_times[i]
            if event_time <= self.current_time:
                i += 1
                continue
            # apply all events at event_time
            for (name, value) in self._events.get(event_time, []):
                try:
                    v = env.get(name)
                    if isinstance(v, TemporalVar):
                        v.set_at(event_time, value)
                    else:
                        # replace plain var with TemporalVar preserving old value at time 0
                        old = v
                        tv = TemporalVar(name, old, self)
                        tv.set_at(event_time, value)
                        env.set(name, tv)
                except NameError:
                    # variable didn't exist before; create it as TemporalVar with no prior value
                    tv = TemporalVar(name, None, self)
                    tv.set_at(event_time, value)
                    env.set(name, tv)
            i += 1

        # remove applied times up to t from _events and _sorted_times
        to_remove = [et for et in self._sorted_times if et <= t]
        for et in to_remove:
            self._events.pop(et, None)
            idx = bisect.bisect_left(self._sorted_times, et)
            if idx < len(self._sorted_times) and self._sorted_times[idx] == et:
                self._sorted_times.pop(idx)

        self.current_time = t

    def step_forward(self, seconds: float, env: 'Environment'):
        newt = self.current_time + float(seconds)
        self.run_to(newt, env)

    def step_backward(self, seconds: float):
        newt = max(0.0, self.current_time - float(seconds))
        self.current_time = newt


# Helpers (AST tokens/names)
def get_name(node):
    """Return single name or dotted name string from Token or Tree."""
    if isinstance(node, Token):
        return node.value
    if isinstance(node, Tree):
        # For dotted_name or similar: join NAME tokens with "."
        names = []
        for c in node.children:
            if isinstance(c, Token) and c.type == "NAME":
                names.append(c.value)
            elif isinstance(c, Tree):
                v = get_name(c)
                if v:
                    names.append(v)
        if names:
            return ".".join(names)
    return None


def extract_name_tokens(node):
    """Return a list of NAME/TYPE token string values found in node."""
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
    Conservative static type checker. Extended to tolerate test/assert/send/recv/type_expr/go/temporal.
    """
    def __init__(self, tree: Tree, infer: bool = False):
        self.tree = tree
        self.infer = infer
        self.functions = {}
        self.builtins = {
            "print": {"param_types": ["auto"], "return_type": "void"},
            "make": {"param_types": ["auto"], "return_type": "chan"},
            # prob module builtins (conservative)
            "prob.uniform": {"param_types": ["float", "float"], "return_type": "auto"},
            "prob.normal": {"param_types": ["float", "float"], "return_type": "auto"},
            "prob.bernoulli": {"param_types": ["float"], "return_type": "auto"},
            "prob.binomial": {"param_types": ["auto"], "return_type": "auto"},
            "prob.infer": {"param_types": ["auto", "auto"], "return_type": "auto"},
            # ml module builtins (conservative)
            "ml.tensor": {"param_types": ["auto"], "return_type": "auto"},
            "ml.linear_regression_train": {"param_types": ["auto", "auto"], "return_type": "auto"},
            "tensor": {"param_types": ["auto"], "return_type": "auto"},
        }

    # Top-level check

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

    # Collect function signature

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

    # Check function body

    def _check_function(self, sig):
        if sig.get("checked"):
            return
        local_types = {pname: sig["param_types"][i] if i < len(sig["param_types"]) else "auto"
                       for i, pname in enumerate(sig["params"])}

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

    # Check statements

    def _check_stmt(self, stmt, local_types, returns):
        if isinstance(stmt, Token):
            return
        if stmt.data in ("var_assign", "temporal_decl"):
            name_tok = stmt.children[0]
            expr = stmt.children[1] if len(stmt.children) > 1 else None
            et = self._expr_type(expr, local_types) if expr else "unknown"
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
        if stmt.data in ("expr_stmt", "go_stmt"):
            self._expr_type(stmt.children[0], local_types)
            return
        if stmt.data == "test_def":
            block = stmt.children[1]
            for s in block.children:
                self._check_stmt(s, local_types.copy(), [])
            return
        if stmt.data == "assert_stmt":
            self._expr_type(stmt.children[0], local_types)
            return

    # Expression type

    def _expr_type(self, node, local_types):
        if isinstance(node, Token):
            if node.type == 'NUMBER':
                return "float" if '.' in node.value else "int"
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
        if node.data == "list_literal":
            elem_types = [self._expr_type(e, local_types) for e in node.children]
            return ListType(elem_types[0] if elem_types else None)
        if node.data == 'var':
            name = get_name(node.children[0])
            return local_types.get(name, "unknown")
        if node.data in ('add', 'sub', 'mul', 'div'):
            left = self._expr_type(node.children[0], local_types)
            right = self._expr_type(node.children[1], local_types)
            if left in {"int", "float"} and right in {"int", "float"}:
                return "float" if "float" in (left, right) else "int"
            if left == "auto" or right == "auto":
                return "auto"
            if node.data == 'add' and left == "string" and right == "string":
                return "string"
            raise TypeError(f"Type error in arithmetic: {left} {node.data} {right}")
        if node.data in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
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

            # handle dotted/method calls
            if '.' in fname:
                # conservative: assume returns float
                return "float"

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

            return fsig.get("inferred_return") or fsig.get("return_type_decl") or "auto"

        if node.data == 'type_expr':
            names = extract_name_tokens(node)
            if len(names) >= 2 and names[0] == 'chan':
                return f"chan<{names[1]}>"
            return "chan"
        if node.data == 'send':
            self._expr_type(node.children[1], local_types)
            return "void"
        if node.data == 'recv':
            return "unknown"
        if node.data == 'expr':
            return self._expr_type(node.children[0], local_types)

        raise NotImplementedError(f"TypeChecker: unsupported node {node.data}")


# Probabilistic module

# Lightweight Distribution abstractions (no external SciPy dependency)
class Distribution:
    def sample(self, rng):
        raise NotImplementedError()
    def logpdf(self, x):
        raise NotImplementedError()

class Uniform(Distribution):
    def __init__(self, a, b):
        assert b > a
        self.a = float(a); self.b = float(b)
    def sample(self, rng):
        return rng.uniform(self.a, self.b)
    def logpdf(self, x):
        if self.a <= x <= self.b:
            return -_math.log(self.b - self.a)
        return -_math.inf

class Normal(Distribution):
    def __init__(self, mu, sigma):
        self.mu = float(mu); self.sigma = float(sigma)
    def sample(self, rng):
        return rng.normal(self.mu, self.sigma)
    def logpdf(self, x):
        return -0.5 * _math.log(2*_math.pi*(self.sigma**2)) - 0.5*((x - self.mu)**2)/(self.sigma**2)

class Bernoulli(Distribution):
    def __init__(self, p):
        self.p = p
    def sample(self, rng):
        return 1 if rng.uniform() < self.p else 0
    def logpmf(self, k):
        if k not in (0,1): return -_math.inf
        return _math.log(self.p if k==1 else 1-self.p)

class BinomialLikelihood:
    def __init__(self, theta_ref, n, observed=None):
        # theta_ref: Distribution instance (prior) or numeric (rare)
        self.theta_ref = theta_ref
        self.n = int(n)
        self.observed = None if observed is None else int(observed)
    def loglik(self, theta_value):
        # log P(data | theta)
        if self.observed is None:
            return 0.0  # no data
        k = self.observed
        # Use log binomial pmf: log C(n,k) + k log(theta) + (n-k) log(1-theta)
        if theta_value <= 0 or theta_value >= 1:
            # handle edge cases numerically
            if k == 0 and theta_value == 0:
                return 0.0
            if k == self.n and theta_value == 1:
                return 0.0
            return -_math.inf
        comb = _math.lgamma(self.n + 1) - _math.lgamma(k + 1) - _math.lgamma(self.n - k + 1)
        return comb + k*_math.log(theta_value) + (self.n - k)*_math.log(1 - theta_value)

class Posterior:
    def __init__(self, samples, weights=None):
        self.samples = _np.asarray(samples)
        self.weights = None if weights is None else _np.asarray(weights)
    def mean(self):
        if self.weights is None:
            return float(self.samples.mean())
        return float((self.samples * self.weights).sum() / self.weights.sum())
    def credible_interval(self, alpha=0.05):
        if self.weights is None:
            lo = float(_np.percentile(self.samples, 100*alpha/2))
            hi = float(_np.percentile(self.samples, 100*(1-alpha/2)))
            return lo, hi
        order = _np.argsort(self.samples)
        s = self.samples[order]
        w = self.weights[order].cumsum()
        w = w / w[-1]
        lo = float(_np.interp(alpha/2, w, s))
        hi = float(_np.interp(1-alpha/2, w, s))
        return lo, hi

def importance_sampling(prior_dist, likelihoods, nsamples=2000, seed=None):
    rng = _np.random.default_rng(seed)
    samples = []
    log_weights = []
    for i in range(nsamples):
        theta = prior_dist.sample(rng)
        logw = 0.0
        for lik in likelihoods:
            logw += lik.loglik(theta)
        samples.append(theta)
        log_weights.append(logw)
    log_weights = _np.array(log_weights)
    maxlw = float(log_weights.max())
    w = _np.exp(log_weights - maxlw)
    w_sum = w.sum()
    if w_sum == 0:
        # fallback to uniform weights if numerical underflow
        w = _np.ones_like(w) / len(w)
    else:
        w = w / w_sum
    return Posterior(_np.array(samples), w)

def metropolis_hastings(prior_dist, likelihoods, nsamples=2000, burn=500, thin=1, proposal_scale=0.1, seed=None):
    rng = _np.random.default_rng(seed)
    x = prior_dist.sample(rng)
    samples = []
    current_logpost = prior_dist.logpdf(x) + sum(l.loglik(x) for l in likelihoods)
    for i in range(nsamples * thin + burn):
        proposal = x + rng.normal(0, proposal_scale)
        lp = prior_dist.logpdf(proposal)
        if lp == -_math.inf:
            accept = False
        else:
            prop_logpost = lp + sum(l.loglik(proposal) for l in likelihoods)
            log_alpha = prop_logpost - current_logpost
            accept = (_math.log(rng.uniform()) < log_alpha)
        if accept:
            x = proposal
            current_logpost = prop_logpost
        if i >= burn and ((i - burn) % thin == 0):
            samples.append(x)
    return Posterior(_np.array(samples))

class ProbModule:
    def uniform(self, a, b):
        return Uniform(a, b)
    def normal(self, mu, sigma):
        return Normal(mu, sigma)
    def bernoulli(self, p):
        return Bernoulli(p)
    def binomial(self, theta_ref, n=1, observed=None):
        return BinomialLikelihood(theta_ref, n, observed)
    def infer(self, param, observations, method="importance", nsamples=2000, **kwargs):
        if method == "importance":
            return importance_sampling(param, observations, nsamples=nsamples, **kwargs)
        elif method in ("mh", "mcmc"):
            return metropolis_hastings(param, observations, nsamples=nsamples, **kwargs)
        else:
            raise ValueError("Unknown inference method: " + str(method))

# single shared instance to attach to interpreter globals later
_prob_instance = ProbModule()



# Machine Learning Support (tensor + autodiff)


# Lightweight NumPy tensor wrapper used when torch is not available
class NumpyTensor:
    def __init__(self, arr):
        self._arr = _np.asarray(arr, dtype=_np.float32)
    def numpy(self):
        return self._arr
    def shape(self):
        return self._arr.shape
    def __repr__(self):
        return f"NumpyTensor({self._arr!r})"

class MLModule:
    def tensor(self, obj):
        """
        Create a tensor. If torch available, return torch.Tensor with requires_grad=True.
        Otherwise return NumpyTensor wrapper.
        """
        if _torch is not None:
            t = _torch.tensor(obj, dtype=_torch.float32)
            # For inputs we usually don't want requires_grad by default except parameters;
            # but for simplicity allow gradients on constructed tensors:
            t.requires_grad_(True)
            return t
        else:
            return NumpyTensor(obj)

    def to_numpy(self, t):
        if _torch is not None and isinstance(t, _torch.Tensor):
            return t.detach().cpu().numpy()
        if isinstance(t, NumpyTensor):
            return t.numpy()
        if isinstance(t, _np.ndarray):
            return t
        return _np.asarray(t)

    def matmul(self, a, b):
        if _torch is not None and isinstance(a, _torch.Tensor) and isinstance(b, _torch.Tensor):
            return _torch.matmul(a, b)
        return _np.matmul(self.to_numpy(a), self.to_numpy(b))

    def add(self, a, b):
        if _torch is not None and isinstance(a, _torch.Tensor) and isinstance(b, _torch.Tensor):
            return a + b
        return self.to_numpy(a) + self.to_numpy(b)

    def sum(self, a, axis=None):
        if _torch is not None and isinstance(a, _torch.Tensor):
            return a.sum(dim=axis) if axis is not None else a.sum()
        return self.to_numpy(a).sum(axis=axis)

    def mean(self, a, axis=None):
        if _torch is not None and isinstance(a, _torch.Tensor):
            return a.mean(dim=axis) if axis is not None else a.mean()
        return self.to_numpy(a).mean(axis=axis)

    def mse_loss(self, preds, targets):
        if _torch is not None and isinstance(preds, _torch.Tensor) and isinstance(targets, _torch.Tensor):
            return _torch.mean((preds - targets) ** 2)
        p = self.to_numpy(preds)
        t = self.to_numpy(targets)
        return float(((p - t) ** 2).mean())

    def linear_regression_train(self, X, y, epochs=1000, lr=0.01):
        """
        If torch available: train linear model w,b with SGD + autodiff and return (w, b)
        If not: return closed-form linear least squares solution (w, b) using NumPy.
        Returns tuples as numpy arrays / floats.
        """
        X_np = self.to_numpy(X)
        y_np = self.to_numpy(y).reshape(-1)
        # ensure 2D X
        if X_np.ndim == 1:
            X_np = X_np.reshape(-1, 1)

        if _torch is not None:
            # Torch path: convert to tensors (float), enable grad for params
            X_t = _torch.tensor(X_np, dtype=_torch.float32)
            y_t = _torch.tensor(y_np, dtype=_torch.float32).view(-1, 1)
            n_features = X_t.shape[1]
            # initialize weights and bias
            w = _torch.randn(n_features, 1, dtype=_torch.float32, requires_grad=True)
            b = _torch.randn(1, 1, dtype=_torch.float32, requires_grad=True)
            opt = _torch.optim.SGD([w, b], lr=lr)
            for _ in range(int(epochs)):
                opt.zero_grad()
                preds = X_t.matmul(w) + b
                loss = _torch.mean((preds.view(-1) - y_t.view(-1)) ** 2)
                loss.backward()
                opt.step()
            w_np = w.detach().cpu().numpy().reshape(-1)
            b_np = float(b.detach().cpu().numpy().reshape(()))
            return w_np, b_np
        else:
            # closed form via normal equation (with bias)
            n, d = X_np.shape
            X_aug = _np.concatenate([X_np, _np.ones((n, 1))], axis=1)  # last column is bias
            # solve least squares: theta = (X^T X)^-1 X^T y
            try:
                theta = _np.linalg.pinv(X_aug.T @ X_aug) @ (X_aug.T @ y_np)
            except Exception:
                theta = _np.linalg.lstsq(X_aug, y_np, rcond=None)[0]
            w_np = theta[:-1]
            b_np = float(theta[-1])
            return w_np, b_np

# single shared instance
_ml_instance = MLModule()



# Helper to resolve dotted names at runtime
def resolve_dotted(name: str, env: Environment):
    """
    Resolve a dotted name string like 'prob.uniform' in the given env.
    Returns Python object (callable or value). Raises NameError if not found.
    """
    parts = name.split(".")
    first = parts[0]
    obj = env.get(first)
    for attr in parts[1:]:
        try:
            obj = getattr(obj, attr)
        except Exception as e:
            raise NameError(f"Attribute '{attr}' not found on '{'.'.join(parts[:parts.index(attr)])}': {e}")
    return obj


# Interpreter (runtime)
class Interpreter:
    def __init__(self):
        self.parser = Lark(chronos_grammar, parser="lalr", propagate_positions=True)
        self.global_env = Environment()
        # builtins
        self.global_env.set("print", lambda *args: print(*args))
        # expose a trivial sleep for demos (optional)
        try:
            self.global_env.set("sleep", lambda s: _time.sleep(s))
        except Exception:
            pass
        # attach prob module instance for Week 6
        self.global_env.set("prob", _prob_instance)
        # attach ml module instance and top-level tensor shortcut for Week 7
        self.global_env.set("ml", _ml_instance)
        # also expose top-level 'tensor' convenience
        self.global_env.set("tensor", _ml_instance.tensor)

        self.type_info = {}
        # timeline for temporal variables
        self.timeline = Timeline()

    def run(self, src: str, skip_typecheck: bool = False, permissive: bool = False,
            infer: bool = False, execute: bool = True, run_tests: bool = False, time_travel: bool = False):
        """
        Parse, optionally type-check, optionally execute, optionally run tests.
        - execute=False: only type-check (used by `build`).
        - run_tests=True: load module then run tests collected in file.
        - time_travel=True: enter debug REPL after prelude.
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
                self.exec_stmt(node, module_env, permissive)

        if run_tests:
            return self._run_tests(test_nodes, module_env, permissive)

        # optional time-travel debug REPL
        if time_travel:
            self.debug_repl(module_env)
            return None

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

        ok = (failed == 0 and errored == 0)
        return ok

    def exec_stmt(self, node, env: Environment, permissive: bool):
        if isinstance(node, Token):
            return
        
        if node.data == "var_assign":
            # support multiple assignment on LHS: assign_targets "=" expr time_spec?
            lhs = node.children[0]
            expr = node.children[1]
            time_spec = None
            if len(node.children) > 2 and isinstance(node.children[2], Tree) and node.children[2].data == "time_spec":
                time_spec = node.children[2]

            value = self.eval_expr(expr, env, permissive)

            # collect target names (either single NAME or assign_targets Tree)
            if isinstance(lhs, Tree) and lhs.data == "assign_targets":
                targets = [get_name(t) for t in lhs.children]
            elif isinstance(lhs, Token) and lhs.type == "NAME":
                targets = [lhs.value]
            else:
                # fallback: try to extract token name
                targets = [get_name(lhs)]

            if time_spec is not None:
                # schedule whole value to name(s) at the given time.
                # If there are multiple targets, schedule the same value to each name.
                offset = None
                for c in time_spec.children:
                    if isinstance(c, Token) and c.type == "NUMBER":
                        offset = float(c.value)
                        break
                target_time = self.timeline.current_time + (offset if offset is not None else 0.0)
                for tname in targets:
                    self.timeline.schedule(target_time, tname, value)
            else:
                # immediate assignment: if multiple targets, try to unpack iterable value
                try:
                    old_val = None
                    # If only one target, preserve previous TemporalVar behavior
                    if len(targets) == 1:
                        name0 = targets[0]
                        try:
                            old_val = env.get(name0)
                        except NameError:
                            old_val = None
                        if isinstance(old_val, TemporalVar):
                            old_val.set_at(self.timeline.current_time, value)
                        else:
                            env.set(name0, value)
                    else:
                        # multiple targets: attempt to unpack value (tuple/list/ndarray)
                        # Accept Python tuple/list or numpy arrays; if numpy array 1-D and length matches, unpack elements.
                        unpack_vals = None
                        if isinstance(value, (list, tuple)):
                            unpack_vals = list(value)
                        elif hasattr(value, 'tolist'):
                            # numpy arrays and torch tensors may have tolist()
                            try:
                                unpack_vals = value.tolist()
                            except Exception:
                                unpack_vals = None
                        if unpack_vals is None:
                            raise TypeError("Right-hand side is not iterable for multiple assignment")
                        if len(unpack_vals) != len(targets):
                            raise ValueError(f"Cannot unpack {len(unpack_vals)} values into {len(targets)} targets")
                        for tname, val_item in zip(targets, unpack_vals):
                            try:
                                old_val = env.get(tname)
                                if isinstance(old_val, TemporalVar):
                                    old_val.set_at(self.timeline.current_time, val_item)
                                else:
                                    env.set(tname, val_item)
                            except NameError:
                                env.set(tname, val_item)
                except Exception as e:
                    # On error, surface it (consistent with prior behavior)
                    raise

            return None


        if node.data == "temporal_decl":
            # "temporal" NAME "=" expr time_spec?
            name_tok = None
            expr = None
            time_spec = None
            for c in node.children:
                if isinstance(c, Token) and c.type == "NAME" and name_tok is None:
                    name_tok = c
                elif isinstance(c, Tree) and c.data == "time_spec":
                    time_spec = c
                elif isinstance(c, Tree) and expr is None:
                    # treat first non-NAME Tree child as expr
                    expr = c
                elif isinstance(c, Token) and c.type != "NAME" and expr is None:
                    # sometimes literal tokens (NUMBER/STRING) may appear directly
                    expr = c

            if name_tok is None and len(node.children) >= 1 and isinstance(node.children[0], Token):
                name_tok = node.children[0]
            if expr is None and len(node.children) >= 2 and isinstance(node.children[1], Tree):
                expr = node.children[1]

            value = self.eval_expr(expr, env, permissive) if expr is not None else None
            tv = TemporalVar(get_name(name_tok), value, self.timeline)
            env.set(get_name(name_tok), tv)

            if time_spec is not None:
                offset = None
                for c in time_spec.children:
                    if isinstance(c, Token) and c.type == "NUMBER":
                        offset = float(c.value)
                        break
                if offset is not None:
                    target_time = self.timeline.current_time + offset
                    self.timeline.schedule(target_time, get_name(name_tok), value)
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
        
        if node.data == 'list_literal':
            return [self.eval_expr(c, env, permissive) for c in node.children]

        if node.data == 'var':
            name = get_name(node.children[0])
            val = env.get(name)
            # If TemporalVar, return value at current timeline pointer
            if isinstance(val, TemporalVar):
                return val.value_at(self.timeline.current_time)
            return val

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

            # Try to resolve dotted names to methods/attributes
            try:
                func_val = resolve_dotted(fname, env) if "." in fname else env.get(fname)
            except NameError:
                raise NameError(f"Function '{fname}' is not defined")
            # If it's a Python callable (e.g., builtin lambda or prob.* bound method)
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

    def debug_repl(self, env: Environment):
        """
        Simple interactive time-travel debugger.
        Commands:
          forward <seconds>   - step forward (applies scheduled events)
          back <seconds>      - step backward (moves time pointer)
          time                - show current logical time
          show <var>          - show current value of var at pointer
          history <var>       - print full history of temporal var
          times               - list scheduled event times
          quit                - exit debugger
        """
        print("Entering ChronosLang time-travel debugger.")
        print("Commands: forward <s>, back <s>, time, show <var>, history <var>, times, quit")
        while True:
            try:
                line = input("chronos-debug> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0]
            arg = parts[1:] if len(parts) > 1 else []
            try:
                if cmd in ("q", "quit", "exit"):
                    break
                if cmd == "time":
                    print(f"current_time = {self.timeline.current_time}")
                    continue
                if cmd == "forward":
                    seconds = float(arg[0]) if arg else 1.0
                    self.timeline.step_forward(seconds, env)
                    print(f"advanced to {self.timeline.current_time}")
                    continue
                if cmd == "back":
                    seconds = float(arg[0]) if arg else 1.0
                    self.timeline.step_backward(seconds)
                    print(f"moved back to {self.timeline.current_time}")
                    continue
                if cmd == "show" and arg:
                    name = arg[0]
                    try:
                        v = env.get(name)
                        if isinstance(v, TemporalVar):
                            print(v.value_at(self.timeline.current_time))
                        else:
                            print(v)
                    except NameError:
                        print(f"Name '{name}' not found")
                    continue
                if cmd == "history" and arg:
                    name = arg[0]
                    try:
                        v = env.get(name)
                        if isinstance(v, TemporalVar):
                            for t, val in v.history():
                                print(f"{t}: {val}")
                        else:
                            print("not a temporal variable")
                    except NameError:
                        print(f"Name '{name}' not found")
                    continue
                if cmd == "times":
                    print(self.timeline.times())
                    continue
                print("Unknown command")
            except Exception as e:
                print("Error:", e)
        print("Exiting debugger.")


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
    p_run.add_argument("--time-travel", action="store_true", help="Enter time-travel debugger after running prelude")

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
        interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive,
                   infer=args.infer, execute=True, run_tests=False, time_travel=args.time_travel)
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
            ok = interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive,
                            infer=args.infer, execute=True, run_tests=True)
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
    interp.run(src, skip_typecheck=args.skip_typecheck, permissive=args.permissive,
               infer=args.infer, execute=True, run_tests=False, time_travel=getattr(args, "time_travel", False))


if __name__ == '__main__':
    main(sys.argv[1:])

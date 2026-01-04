# ChronosLang — Language Specification & Developer Guide

This document specifies the **ChronosLang** language surface syntax, the behavior of the reference interpreter, and the official tooling shipped in this repository.

---

## Table of contents

1. Introduction & motivation  
2. Running the reference implementation 
3. Language overview (core concepts)  
4. Concrete syntax — examples and idioms  
5. Formal grammar (EBNF & Lark)  
6. Execution model & runtime components  
7. CLI: run / test / build (flags & behavior)  
8. Web runner (FastAPI) & browser UI  
9. Time‑travel engine & debugging tools  
10. Macros, macro expansion, and compile‑time behavior  
11. Reflection API (runtime)  
12. Probabilistic & ML modules  
13. Interactive examples  
14. Error messages & debugging tips  

---

## 1. Introduction & motivation

ChronosLang is a compact, experimental language and reference interpreter built around three interlocking capabilities:

- **Time‑native programming.** ChronosLang treats time as a first‑class concern: *temporal variables* keep a history, assignments can be scheduled for future logical times, and a global **Timeline** supports moving forward and backward through logical time. This enables reproducible time-based demos and practical “time‑travel” inspection of state.

- **Lightweight concurrency.** ChronosLang includes minimal Go‑inspired primitives: `go` for background execution and unbuffered rendezvous channels (`make(chan int)`, `ch <- v`, `<- ch`). The goal is clarity and teachability rather than maximum performance. (Background work uses Python threads; thread interleavings are not deterministic.)

- **Compile‑time macros + runtime reflection.** ChronosLang supports compile‑time macros (AST-level expansion before execution) and a small runtime reflection API (`reflect.*`, `reflect_type`, `reflect_globals`, etc.). This makes it easy to prototype small language extensions, instrumentation, and self-inspection demos.

### Why this project exists

ChronosLang was designed as a *research‑first* and *teaching‑first* interpreter:

- to make temporal reasoning **visible** (value histories, scheduled updates, timeline scrubbing),
- to keep concurrency **small and explainable** (rendezvous channels),
- to enable meta-programming experiments **without** turning the runtime into a large system.

---

## 2. Running the reference implementation

This repository includes the reference interpreter (`chronos/interpreter.py`).

For installation, dependencies, GUI/web setup, and full demo commands, see **README.md**.

Minimal reproducibility commands:

```bash
# run the default example (examples/hello.chronos)
python chronos/interpreter.py

# run a specific file
python chronos/interpreter.py examples/temporal_demo.chronos

# enter the CLI time-travel REPL (after running the prelude)
python chronos/interpreter.py examples/temporal_demo.chronos --time-travel

# run tests
python chronos/interpreter.py test examples/tests_and_packages.chronos

# run Time‑travel debugger GUI (PyQt5)
python chronos_time_travel_gui.py

---

## 3. Language overview (core concepts)

ChronosLang is small by design. The reference interpreter provides:

- **Functions** with Python‑like indentation blocks.
- **A conservative static type checker** with optional permissive mode for demos.
- **Channels + `go`** for lightweight concurrency.
- **Temporal variables** and scheduled assignments for time-indexed state.
- **Compile‑time macros** (expanded before execution).
- **Runtime reflection** for inspecting the environment and timeline.
- **Optional `prob.*` and `ml.*` modules** for probabilistic and ML demos.

### 3.1 Feature map (what to run)

| Feature | Demo file | Typical command |
|---|---|---|
| Basics (functions, arithmetic) | `examples/hello.chronos` | `python chronos/interpreter.py examples/hello.chronos` |
| Type checking + permissive mode | `examples/type_system.chronos` | `... type_system.chronos [--permissive]` |
| Concurrency (go + channels) | `examples/producer_consumer.chronos` | `... producer_consumer.chronos` |
| Temporal engine | `examples/temporal_demo.chronos` | `... temporal_demo.chronos --time-travel` |
| Probabilistic module | `examples/prob_coin.chronos` | `... prob_coin.chronos` |
| ML module | `examples/tensor_linear_regression.chronos` | `... tensor_linear_regression.chronos` |
| Macros + reflection | `examples/macros_reflection.chronos` | `... macros_reflection.chronos --dump-expanded` |
| Tests | `examples/tests_and_packages.chronos` | `python chronos/interpreter.py test ...` |

---

## 4. Concrete syntax — examples and idioms

ChronosLang source is written with **indentation blocks**, but the reference implementation converts indentation into explicit `{ ... }` blocks during preprocessing before parsing (Section 5.2). Users write indentation; the braces are an internal parser representation.

### 4.1 Functions & expressions

```chronos
function add(a: int, b: int) -> int:
    return a + b

function auto_add(a, b):
    return a + b

print(add(5, 6))
print(auto_add("hi", " world"))
```

Notes:
- `return` is required to produce a value from a function.
- Type annotations are optional; omitted types behave like `auto` in the type checker.

### 4.2 Variables & temporal variables

```chronos
x = 42

temporal y = 10
y = 20 @ t+3s

print(y)
```

Notes:
- `temporal` introduces a **TemporalVar** whose value can be queried at different logical times.
- `@ t+Ns` schedules an assignment **relative** to the current logical time (seconds; fractions are allowed, e.g., `t+0.5s`).

### 4.3 Concurrency — `go` and channels

```chronos
function worker(ch):
    data = <-ch
    ch <- data * 2

ch = make(chan int)
go worker(ch)

ch <- 42
print(<-ch)
```

Notes:
- Channels are **unbuffered rendezvous** channels: send blocks until a receiver is ready.
- `go` spawns a background thread. Thread scheduling is not deterministic.

### 4.4 Macros (compile‑time)

```chronos
macro log(expr):
    print("value:", expr)

x = 10
log(x)
```

Notes:
- Macros expand **before** runtime execution.
- Macro definitions are removed from the runtime AST after expansion.

### 4.5 Reflection (runtime)

```chronos
function show_reflection():
    print("func:", reflect_func_name())
    print("locals:", reflect_locals())
    print("timeline:", reflect.timeline())
    print("inspect x:", reflect.inspect("x"))

show_reflection()
```

Notes:
- Reflection inspects runtime environment and timeline.
- Some information (like locals) only exists while a function is executing.

### 4.6 Probabilistic programming

```chronos
theta = prob.uniform(0.0, 1.0)
obs = prob.binomial(theta, 10, 7)
posterior = prob.infer(theta, [obs], "importance", 5000)
print("Posterior mean:", posterior.mean())
```

### 4.7 ML / tensor example

```chronos
X = tensor([[1.0], [2.0], [3.0]])
y = tensor([[2.0], [4.0], [6.0]])

w, b = ml.linear_regression_train(X, y, 500, 0.01)

y_pred = ml.add(ml.matmul(X, w), b)
loss = ml.mse_loss(y_pred, y)
print("Loss:", loss)
```

---

## 5. Formal grammar (EBNF & Lark)

ChronosLang is parsed by **Lark** using a compact grammar. User-written indentation is converted to `{ ... }` blocks before parsing (Section 5.2).

### 5.1 Core grammar (EBNF-style)

The following is aligned with the grammar embedded in `chronos/interpreter.py`:

```ebnf
start         ::= stmt*

stmt          ::= var_assign
                | temporal_decl
                | func_def
                | macro_def
                | return_stmt
                | expr_stmt
                | go_stmt
                | test_def
                | assert_stmt

dotted_name   ::= NAME ("." NAME)*
func_call     ::= dotted_name "(" args? ")"
args          ::= expr ("," expr)*

func_def      ::= "function" NAME "(" params? ")" return_type? ":" block
params        ::= param ("," param)*
param         ::= NAME (":" TYPE)?
return_type   ::= "->" TYPE

block         ::= "{" stmt* "}"

macro_def     ::= "macro" NAME "(" params? ")" ":" block

temporal_decl ::= "temporal" NAME "=" expr time_spec?
assign_targets ::= NAME ("," NAME)*
var_assign    ::= assign_targets "=" expr time_spec?

return_stmt   ::= "return" expr
expr_stmt     ::= expr
go_stmt       ::= "go" expr

test_def      ::= "test" STRING ":" block
assert_stmt   ::= "assert" expr

list_literal  ::= "[" [expr ("," expr)*] "]"

send          ::= NAME "<-" expr
recv          ::= "<-" NAME

type_expr     ::= "chan" TYPE
time_spec     ::= "@" "t" "+" NUMBER ("s")?
```

Terminals:
- `NAME`  = `/[a-zA-Z_][a-zA-Z0-9_]*/`
- `NUMBER` = signed integer or float (e.g., `2`, `0.5`)
- `STRING` = escaped string

### 5.2 Preprocessor: indentation → braces

The reference interpreter uses a small preprocessor to convert Python-style indentation into explicit braces for the parser. This is intentionally minimal and designed for short demo programs.

(Implementation: `preprocess_indent()` in `chronos/interpreter.py`.)

### 5.3 Parser integration

The parser is constructed as:

```python
parser = Lark(chronos_grammar, parser="lalr", propagate_positions=True)
```

`propagate_positions=True` preserves line/column info for useful errors.

---

## 6. Execution model & runtime components

ChronosLang execution is a pipeline:

1. **Preprocess** indentation → braces  
2. **Parse** into an AST  
3. **Collect + expand macros** (compile‑time)  
4. **Type check** (optional; can be skipped)  
5. **Execute** prelude statements; optionally run tests; optionally enter CLI time‑travel REPL  

### 6.1 Prelude vs tests

- Top-level `test "...":` blocks are **not executed** during a normal run; they are executed only in **test mode** (`python chronos/interpreter.py test ...` or the web `/run_test` endpoint).
- `assert <expr>` is a real statement and will raise `AssertionError` if false wherever it runs.

### 6.2 Temporal runtime (Timeline + TemporalVar)

- A **TemporalVar** stores a sorted history of values: `(time, value)` pairs.
- The **Timeline** stores scheduled events and advances logical time with `run_to()` / stepping.
- Rewinding time moves the **time pointer**; it does not “undo” I/O or reverse threads.

### 6.3 Concurrency

- `go f(...)` spawns a Python thread that calls the Chronos function `f`.
- Channels are **unbuffered rendezvous** objects; send/receive are synchronized handoffs.
- Concurrency is **not** time‑traveled. For deterministic “scrub‑able” demos, prefer temporal scheduling over threads.

### 6.4 Reflection

The global `reflect` object provides:
- `reflect.vars()`, `reflect.functions()`
- `reflect.timeline()`
- `reflect.inspect(name)`
- `reflect.macros()` (names only)

Helper functions:
- `reflect_type(x)`, `reflect_globals()`, `reflect_func_name()`, `reflect_locals()`

---

## 7. CLI: run / test / build (flags & behavior)

### 7.1 Common patterns

Legacy form (common in docs and examples):

```bash
python chronos/interpreter.py <file>.chronos [flags...]
```

Subcommand form:

```bash
python chronos/interpreter.py run <file>.chronos [flags...]
python chronos/interpreter.py test <file-or-dir>
python chronos/interpreter.py build <path>
```

### 7.2 Flags (public, recommended)

- `--permissive`  
  Continue past certain static/runtime type errors by downgrading them to warnings (better for demos and the web runner).

- `--skip-typecheck`  
  Skip static type checking entirely.

- `--time-travel`  
  After executing prelude statements, enter the **CLI time‑travel REPL** (timeline control + state inspection).

- `--dump-expanded`  
  Print the AST after macro expansion (useful for explaining macros and debugging expansion).

> Note on `--infer`  
> The codebase currently contains an `--infer` flag, but it is **not needed for normal use**, and it is intentionally omitted from the main user documentation here to keep the CLI simple.

---

## 8. Web runner (FastAPI) & browser UI

ChronosLang ships a FastAPI app in `server/app.py` and a simple browser UI in `web/index.html`.

### 8.1 Two execution modes

The server supports two distinct modes:

1) **One‑shot runs (subprocess mode)**  
   Endpoints like `/run` and `/upload` write code to a temp file and execute the interpreter in a subprocess. This is simple and returns `stdout/stderr/returncode`.

2) **Interactive time sessions (in‑process mode)**  
   Endpoints like `/start_session` create an in‑process Interpreter + module environment and keep it in memory under a `session_id`. Then `/step_forward`, `/step_back`, `/show`, `/history`, `/times`, and `/call_function` operate on that live session.

### 8.2 Endpoints (as implemented)

- `POST /run` — run code (JSON or form), subprocess mode
- `POST /upload` — run uploaded `.chronos`, subprocess mode
- `POST /run_test` — run tests in an uploaded `.chronos`, subprocess mode
- `POST /start_session` — create a live in‑process session from code, returns `session_id`
- `POST /step_forward` — advance session timeline by N seconds
- `POST /step_back` — rewind session time pointer by N seconds
- `GET /show` — show current value of a variable in a session
- `GET /history` — show temporal history of a variable in a session
- `GET /times` — list scheduled times in a session
- `POST /call_function` — call a function in a session
- `POST /end_session` — destroy session
- `GET /health` — server health
- `GET /` — serves the browser UI (if present)

### 8.3 Security note (important)

Running user-provided code on a server is dangerous. The current web runner is suitable for **local demos**. For public hosting, you should sandbox execution (containers, resource limits, restricted filesystem/network, etc.).

---

## 9. Time‑travel engine & debugging tools

This section covers (A) the engine semantics and (B) the official interfaces.

### 9.1 Engine semantics (language/runtime)

- `temporal x = v` creates a TemporalVar with a value history.
- `x = v @ t+Ns` schedules an update at logical time `current_time + N`.
- Advancing the timeline applies events in timestamp order.
- Rewinding does not reverse I/O; it changes what value is *queried* at that time.

### 9.2 CLI time‑travel REPL (`--time-travel`)

When you run with `--time-travel`, the interpreter executes prelude code first, then enters a terminal REPL that lets you:
- move time forward/back by a given delta,
- inspect current values,
- inspect full temporal histories.

This is a **CLI interface** to the time‑travel engine.

### 9.3 Time‑travel debugger (GUI) — `chronos_time_travel_gui.py`

The PyQt5 tool is a **desktop visualizer/debugger**:
- It parses the program and executes **prelude nodes** into an environment.
- It displays temporal variable histories and lets you scrub time with a slider.
- It does **not** perform compile-time macro expansion, so macro-dependent programs may not behave the same as in the CLI.

### 9.4 Web time sessions

The web session endpoints provide time travel via HTTP:
- a session holds an interpreter + environment,
- stepping endpoints call timeline operations,
- show/history endpoints query the session state.

---

## 10. Macros, macro expansion, and compile‑time behavior

Macros are compile‑time AST transformations.

- Macro definitions are collected first and removed from the runtime AST.
- Macro calls are expanded conservatively:
  - top-level macro calls can splice multiple statements,
  - some expression-position macros can expand to a single expression.

Use `--dump-expanded` to see the expanded AST.

**Macro hygiene:** nodes are deep-copied, but there is no automatic alpha-renaming. Avoid accidental name capture.

---

## 11. Reflection API (runtime)

ChronosLang exposes:

- `reflect.vars(env=None)`  
- `reflect.functions(env=None)`  
- `reflect.timeline()`  
- `reflect.inspect(name, env=None)`  
- `reflect.macros()` (names only)

Helper functions:
- `reflect_type(x)`
- `reflect_globals()`
- `reflect_func_name()`
- `reflect_locals()`

Caveat: locals and current function name depend on the active call frame.

---

## 12. Probabilistic & ML modules

### 12.1 `prob.*`

`prob` provides lightweight distributions and inference helpers (importance sampling and MCMC-style methods). It is designed for clear toy demos (coin flips, binomial observations).

### 12.2 `ml.*`

`ml` provides a minimal tensor API with an optional PyTorch backend. If PyTorch is available, training uses autodiff; otherwise it falls back to NumPy-based computations.

A top-level convenience function `tensor(...)` is also available.

---

## 13. Interactive examples

Screenshots are stored under `docs/screenshots/`.

- `examples/hello.chronos`  
  ![hello output](docs/screenshots/hello.chronos_output.png)

- `examples/type_system.chronos`  
  ![type_system static](docs/screenshots/type_system_chronos_static_mode_output.png)  
  ![type_system permissive](docs/screenshots/type_system_chronos_permissive_mode_output.png)

- `examples/producer_consumer.chronos`  
  ![producer_consumer output](docs/screenshots/producer_consumer.chronos_output.png)

- `examples/tests_and_packages.chronos`  
  ![tests output](docs/screenshots/tests_and_packages.chronos_output.png)

- `examples/temporal_demo.chronos`  
  ![temporal_demo output](docs/screenshots/temporal_demo.chronos_output.png)

- `examples/prob_coin.chronos`  
  ![prob_coin output](docs/screenshots/prob_coin.chronos_output.png)

- `examples/tensor_linear_regression.chronos`  
  ![tensor LR output](docs/screenshots/tensor_linear_regression.chronos_output.png)

- `examples/macros_reflection.chronos`  
  ![macros_reflection output](docs/screenshots/macros_reflection.chronos_output.png)

---

## 14. Error messages & debugging tips

### Parsing problems
- Most parse errors come from indentation or missing `:` on block headers.
- If needed, inspect the preprocessed braces form by printing `preprocess_indent(src)` in Python.

### Name errors
- Define variables/functions before use.
- For temporal variables, declare `temporal x = ...` if you want a meaningful value at `t=0`.

### Type issues
- Use `--permissive` for demos to downgrade some errors to warnings.
- Use `--skip-typecheck` for fastest experimentation (expect more runtime errors).

### Macro confusion
- Remember: macros are compile‑time.
- Use `--dump-expanded` to show what the macro produced.

### Time-travel expectations
- Rewinding time does not undo prints or background threads.
- For scrub-friendly demos, prefer scheduled assignments over thread timing.

### Web runner issues
- Run `uvicorn server.app:app --reload` from the repo root.
- Treat public deployment as unsafe unless you sandbox code execution.

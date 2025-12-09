# ChronosLang

ChronosLang is a Python-inspired research programming language with native time-travel debugging,
supporting temporal variables, scheduled assignments, deterministic concurrency, compile-time macros,
runtime reflection, static typing, built-in testing, and lightweight probabilistic/ML modules and
scientific computing.

ChronosLang is a small, research-first language that centers on three interlocking capabilities:

- **Time-native programming.** Temporal variables hold histories; assignments may be scheduled  
  (e.g., `x = 5 @ t+2s`); a `Timeline` records events so tools can scrub, replay, and inspect past  
  and future logical states.

- **Lightweight deterministic concurrency.** `go` for background execution plus unbuffered  
  rendezvous channels (`make(chan)`, `ch <- v`, `<- ch`) provide deterministic concurrency suitable  
  for demos and teaching.

- **Compile-time macros & runtime reflection.** Hygienic-ish macros allow AST-level compile  
  transforms; `reflect.*` APIs let running programs inspect globals, functions, and temporal  
  timelines.

---

## Highlights & goals

ChronosLang is designed to:

- Run example programs with functions, arithmetic, and print statements.
- Support **static typing / type inference**.
- Provide **deterministic goroutine-style concurrency and rendezvous channels**.
- Offer **built-in testing & a simple package runner**.
- Support **temporal variables and scheduled assignments**.
- Include lightweight **probabilistic (`prob.*`) and ML (`ml.*`) modules** for demo examples.
- Enable **time-travel debugging & deterministic replay**.
- Provide **compile-time macros** (expanded before runtime) and a small **reflection API**.
- Ship with tools: **CLI runner**, **FastAPI web-runner**, and a **PyQt5 time-travel debugger GUI**.

---

## Installation & setup

Clone the repository:

```bash
git clone https://github.com/Srijon25/ChronosLang.git
cd ChronosLang
```

Create and activate a virtual environment:

```bash
python -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows (PowerShell)
.\env\Scripts\Activate.ps1
```

Install core dependencies:

```bash
pip install lark-parser numpy
```

Optional packages for full functionality:

```bash
pip install pyqt5 fastapi uvicorn torch
```

---

## Quickstart (run & test)

### Run example programs

```bash
# run the default example (hello.chronos)
python chronos/interpreter.py examples/hello.chronos

# run with static typing
python chronos/interpreter.py examples/type_system.chronos

# run producer/consumer concurrency demo
python chronos/interpreter.py examples/producer_consumer.chronos

# run temporal demo and enter CLI time-travel REPL
python chronos/interpreter.py examples/temporal_demo.chronos --time-travel

# dump expanded AST after macros
python chronos/interpreter.py examples/macros_reflection.chronos --dump-expanded

# run any other example file
python chronos/interpreter.py examples/<file>.chronos
```

### Run tests

```bash
python chronos/interpreter.py test examples/tests_and_packages.chronos
# or
python chronos/interpreter.py test examples
```

---

## Time-travel debugger GUI (PyQt5)

Start the GUI prototype:

```bash
python chronos_time_travel_gui.py
```

The GUI lets you:

- Load `.chronos` programs.
- Execute the prelude to schedule temporal events.
- Scrub logical time with a slider and step controls.
- List variables (distinguishing temporal vs non-temporal).
- Inspect temporal histories for the selected variable.

- ✅ Scrubs temporal histories.  
- ❌ Cannot display macro expansions or reflection values that depend on active function calls.

---

## Web runner (FastAPI browser UI)

Start the web runner:

```bash
uvicorn server.app:app --reload --port 8000
```

Then open in your browser:

```text
http://127.0.0.1:8000
```

You can:

- Write and edit ChronosLang code in the browser.
- Run examples through the FastAPI backend.
- View stdout/stderr and return codes in the web UI.

---

## Demo recordings

Demo videos live in `docs/recordings/`:

- **Time-travel debugging GUI**  
  `docs/recordings/python_chronos_time_travel_debugger_gui.mp4`

- **Web runner**  
  `docs/recordings/ChronosLang_web_runner_full_demo.chronos.mp4`

(On GitHub these will appear as downloadable video files.)

---

## Primary commands and flags

**Commands:**

- `run <file>` — parse → macro-expand → (typecheck) → execute prelude.  
  (Invoked via `python chronos/interpreter.py <file>` in this implementation.)

- `test <path|dir>` — run `test "name":` blocks in isolation.

- `build <path>` — type-check only (no execution) if implemented.

**Flags:**

- `--permissive` — relax strict type checking (recommended for demos).
- `--skip-typecheck` — skip static checking.
- `--time-travel` — enter interactive time-travel REPL after prelude.
- `--trace <file>` — record deterministic execution trace (if enabled).
- `--replay <trace.json>` — replay a previously recorded trace (if enabled).
- `--dump-expanded` — show AST/source after macro expansion.

**Exit codes:**

- `0` on success.
- Non-zero for failures (e.g., failing tests or uncaught exceptions).

---

## Project layout

```text
ChronosLang/
├── chronos/
│   ├── interpreter.py          # main interpreter, timeline, macros, reflection, etc.
│   ├── lexer.py                # Lark grammar + indentation → braces preprocessor
│
├── examples/
│   ├── hello.chronos
│   ├── macros_reflection.chronos
│   ├── prob_coin.chronos
│   ├── producer_consumer.chronos
│   ├── temporal_demo.chronos
│   ├── tensor_linear_regression.chronos
│   ├── tests_and_packages.chronos
│   └── type_system.chronos
│
├── docs/
│   ├── recordings/
│   │   ├── ChronosLang_web_runner_full_demo.chronos.mp4
│   │   └── python_chronos_time_travel_debugger_gui.mp4
│   └── screenshots/
│       ├── hello.chronos_output.png
│       ├── macros_reflection.chronos_output.png
│       ├── prob_coin.chronos_output.png
│       ├── producer_consumer.chronos_output.png
│       ├── temporal_demo.chronos_output.png
│       ├── tensor_linear_regression.chronos_output.png
│       ├── tests_and_packages.chronos_output.png
│       ├── type_system_chronos_permissive_mode_output.png
│       └── type_system_chronos_static_mode_output.png
│
├── server/
│   └── app.py                  # FastAPI server
│
├── web/
│   └── index.html              # frontend for web runner
│
├── chronos_time_travel_gui.py  # PyQt5 time-travel debugger GUI
├── CITATION.cff
├── LICENSE
├── README.md
└── spec.md
```

---

## Documentation

- Full language specification: `spec.md`  
- Screenshots & demos: `docs/`  
- Example programs: `examples/`  

---

## Examples & core features

Run included programs from `examples/`:

- **Hello & Functions** — `hello.chronos`, `type_system.chronos`  
  - Basic functions, expressions, type inference (`add()`, `auto_add()`).

- **Concurrency** — `producer_consumer.chronos`  
  - `go` routines and channels (`<-`, `ch <- value`).  
  - Deterministic concurrency examples.

- **Unit Tests & Package System** — `tests_and_packages.chronos`  
  - `test "<name>":` blocks with `assert` statements.  
  - Run tests using `python chronos/interpreter.py test examples/`.  
  - Mixes code and tests in the same file; supports reproducible test-driven demos.

- **Temporal Variables & Time-Travel** — `temporal_demo.chronos`  
  - `temporal` variables, scheduled assignments (`@ t+Ns`).  
  - Scrub history with `--time-travel`.

- **Probabilistic Programming** — `prob_coin.chronos`  
  - `prob.*` API, inference backends (importance sampling, MCMC).

- **Machine Learning / Tensors** — `tensor_linear_regression.chronos`  
  - Tensor ops, linear regression, MSE loss; optional PyTorch backend.

- **Macros & Reflection** — `macros_reflection.chronos`  
  - Compile-time macros (logging, type inspection).  
  - Runtime reflection (function names, locals, timeline, variable inspection).

---

## Time-travel debugging GUI details

The GUI supports:

- Visualizing timelines and temporal variable histories.  
- Stepping forward/backward through execution.  
- Inspecting program state dynamically.

- ✅ Scrubs temporal histories  
- ❌ Cannot display macro expansions or reflection inside active function calls.

---

## Best practices

- Define macros at the top of files so they are collected before use.
- Use `--permissive` in demos to avoid runtime type errors.
- For reversible demos, prefer temporal variables + scheduled assignments instead of goroutines.
- Reserve goroutines and channels for examples where reversibility is not required.

---

## The `lexer.py` utility (diagnostic parse-tree printer)

`chronos/lexer.py` is a small developer-facing utility for grammar/debugging:

- It is **not** required to run examples. All example programs run with the interpreter
  (`chronos/interpreter.py`) without using `lexer.py`.
- It shares the same grammar and preprocessor as the interpreter and prints the parse for a
  `.chronos` source file.

Typical use:

```bash
# print parse tree
python chronos/lexer.py examples/hello.chronos
```

This is helpful when:

- developing grammar changes,
- writing macros,
- or teaching how the language is parsed.

---

## Roadmap (development history)

ChronosLang evolved through a structured, research-first development plan:

- **Week 1 – Interpreter Core**
  - Added ChronosLang interpreter capable of running example files with functions, arithmetic,
    and print statements.
  - Introduced a lexer and `hello.chronos` for parse tree output.

- **Week 2 – Static Type System**
  - Integrated a static type system with type inference.
  - Added permissive mode for testing.
  - Added `examples/type_system.chronos` demonstrating type annotations and inferred expressions.

- **Week 3 – Deterministic Concurrency**
  - Added goroutine-style concurrency with `go`, `make(chan)`, and synchronous send/receive
    (`<-`, `ch <- value`).
  - Added `examples/producer_consumer.chronos`.

- **Week 4 – Unit Testing & Package System**
  - Implemented package manager skeleton (`chronos` run/test/build).
  - Integrated unit test syntax and added `examples/tests_and_packages.chronos` with
    `test "<name>":` blocks and assertions.

- **Week 5 – Temporal Engine & Time-Travel Debugger**
  - Implemented `ChronosEngine` supporting temporal variables and scheduled assignments
    (`x = 5 @ t+2s`).
  - Added `examples/temporal_demo.chronos` showcasing reversible state and timeline scrubbing.

- **Week 6 – Probabilistic Programming**
  - Added `prob.*` module with Bernoulli, Uniform, Normal distributions.
  - Implemented simple inference backends (MCMC / importance sampling).
  - Added Bayesian coin-flip demo: `examples/prob_coin.chronos`.

- **Week 7 – Machine Learning Core**
  - Introduced tensor type with autodiff.
  - Integrated optional PyTorch backend.
  - Added linear regression demo: `examples/tensor_linear_regression.chronos`.

- **Week 8 – Time-Travel Debugger GUI**
  - Added PyQt5 GUI for timeline visualization.
  - Implemented stepping through program execution and inspecting variable states.

- **Week 9 – Macros & Reflection**
  - Implemented compile-time macros (`log`, `debug_type`, `list_globals`).
  - Added runtime reflection API.
  - Added `examples/macros_reflection.chronos`.

- **Week 10 – Web Runner**
  - Added browser-based UI with FastAPI backend to run ChronosLang examples interactively.

- **Week 11 – Documentation & Specification**
  - Published official ChronosLang specification and documentation with curated screenshots.

- **Week 12 – Final Release & Publication**
  - Prepared `README.md` for public release.
  - Published the project on Zenodo to obtain a DOI.

---

## Contributing

Contributions, bug reports, and feature requests are welcome.

- Please check the roadmap before submitting a pull request.
- Open issues for bugs, feature requests, or documentation improvements.

---

## License

MIT License © 2025 Srijon Kumar Shill  

See `LICENSE` for the full text.

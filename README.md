# ChronosLang

ChronosLang is a small, Python‑inspired **research programming language + reference interpreter** focused on three ideas:

- **Time‑native programming** — *temporal variables* keep histories; you can schedule writes like `x = 5 @ t+2s`; a global `Timeline` makes it possible to scrub and inspect logical time.
- **Lightweight concurrency** — `go` for background execution plus unbuffered rendezvous channels (`make(chan int)`, `ch <- v`, `<-ch`) for compact teaching/demo programs.
- **Compile‑time macros + runtime reflection** — macros expand before runtime; reflection APIs let programs inspect globals, functions, and timeline state.

This repo ships:
- a CLI runner (`chronos/interpreter.py`)
- a FastAPI web runner (`server/app.py` + `web/index.html`)
- a PyQt5 desktop time‑travel debugger (`chronos_time_travel_gui.py`)
- examples, screenshots, and a full language spec (`spec.md`)

---
## Note on commit message “Week” labels

In GitHub commit messages I sometimes use labels like **`Week 3: ...`**.  
These “Week” labels are **just milestone levels (build order)** — they are **not real calendar weeks**.


## What “time travel” means in ChronosLang (important)

ChronosLang has one **time‑travel engine**, and multiple **interfaces** to it:

- **Time‑travel engine (runtime):** `Timeline` + `TemporalVar` (the semantics of temporal variables and scheduled assignments).
- **CLI time‑travel REPL:** `--time-travel` enters an interactive terminal mode to step time and inspect state.
- **Web time‑travel sessions:** the server can keep a live interpreter session and expose step/inspect endpoints.
- **Time‑travel debugger GUI:** the PyQt5 app is a *separate desktop tool* that visualizes temporal histories with a slider.

These are related, but not the same thing.

---

## Highlights

ChronosLang is designed to be *small, teachable, and demo‑friendly*:

- Functions, expressions, indentation blocks
- Static typing (conservative) + optional permissive mode for smoother demos
- `go` + rendezvous channels (`<-` / `ch <- v`)
- Built‑in `test "name":` blocks + `assert`
- Temporal variables + scheduled assignments + timeline scrubbing
- `prob.*` and `ml.*` modules for toy probabilistic/ML demos
- Macros (compile‑time) + reflection (runtime)

---

## Installation

### Requirements
- Python **3.13+** recommended

### Setup

```bash
git clone https://github.com/Srijon25/ChronosLang.git
cd ChronosLang

python -m venv venv
# macOS / Linux
source venv/bin/activate
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

> ⚠️ **Windows note (PowerShell fix)**  
> If you see: **“execution of scripts is disabled on this system”**  
> Open PowerShell **as Administrator**, then run: `Set-ExecutionPolicy RemoteSigned`  
> Type **Y** to confirm, then activate again.

python -m pip install --upgrade pip
python -m pip install lark numpy
```

Optional dependencies (install only if you use the feature):

```bash
# GUI debugger
python -m pip install PyQt5

# web runner
python -m pip install fastapi uvicorn

# ML acceleration (optional)
python -m pip install torch
```

---

## Quickstart

### Run example programs

```bash
# run default example (hello.chronos)
python chronos/interpreter.py

# type system demo
python chronos/interpreter.py examples/type_system.chronos
python chronos/interpreter.py examples/type_system.chronos --permissive

# concurrency demo
python chronos/interpreter.py examples/producer_consumer.chronos

# temporal demo + enter CLI time-travel REPL
python chronos/interpreter.py examples/temporal_demo.chronos --time-travel

# macros + reflection (show expanded AST)
python chronos/interpreter.py examples/macros_reflection.chronos --dump-expanded
```

### Run tests

```bash
python chronos/interpreter.py test examples/tests_and_packages.chronos
# or run all tests under examples/
python chronos/interpreter.py test examples
```

---

## Time‑travel debugger GUI (PyQt5)

Start the desktop GUI:

```bash
python chronos_time_travel_gui.py
```

The GUI can:
- load a `.chronos` file,
- execute the program prelude,
- scrub logical time (slider + step controls),
- list variables (temporal vs non‑temporal),
- show full temporal history for a selected variable.

Limitations (expected by design):
- ✅ scrubs temporal histories
- ❌ does not show compile‑time macro expansion
- ❌ reflection values that require an *active call frame* are limited (the GUI is prelude‑oriented)

---

## Web runner (FastAPI + browser UI)

Start the server:

```bash
uvicorn server.app:app --reload --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

### What you can do in the web UI
- edit ChronosLang code in the browser
- run code and see stdout/stderr/return code

### Advanced: interactive time sessions (API)
`server/app.py` also supports live “session” endpoints (create a session, step time forward/back, inspect variables). This enables time‑travel controls from a browser UI.

> Security note: executing user‑supplied code on a server is dangerous. The web runner is intended for **local demos** unless you add sandboxing and resource limits.

---

## Demo recordings

Videos live in `docs/recordings/`:

- **Time‑travel debugger GUI**  
  `docs/recordings/python_chronos_time_travel_debugger_gui.mp4`

- **Web runner**  
  `docs/recordings/ChronosLang_web_runner_full_demo.chronos.mp4`

---

## Commands and flags (CLI)

### Commands
- `run <file>` — parse → macro expand → (typecheck) → execute prelude  
  (In this repo you can also run directly as: `python chronos/interpreter.py <file>`.)
- `test <path|dir>` — execute `test "name":` blocks in isolation
- `build <path>` — typecheck-only workflow (if enabled in your version)

### Flags (commonly used)
- `--permissive` — relax strict type checking for demos
- `--skip-typecheck` — skip static checking
- `--time-travel` — enter the **CLI time‑travel REPL** after the prelude
- `--dump-expanded` — print the expanded AST after macro expansion

> Note: the codebase includes an `--infer` flag, but most users don’t need it; ChronosLang runs fine without it.

Exit codes:
- `0` on success
- non‑zero on failures (e.g., failing tests or uncaught exceptions)

---

## Project layout

```text
ChronosLang/
├── chronos/
│   ├── interpreter.py          # interpreter + timeline + macros + reflection + prob/ml
│   └── lexer.py                # grammar + indentation→braces utility
├── examples/                   # runnable .chronos programs
├── docs/
│   ├── recordings/             # demo videos
│   └── screenshots/            # example outputs
├── server/
│   └── app.py                  # FastAPI server
├── web/
│   └── index.html              # browser UI
├── chronos_time_travel_gui.py  # PyQt5 time-travel debugger GUI
├── paper.md / paper.bib        # paper draft + bibliography
├── spec.md                     # language spec
└── README.md
```

---

## Documentation

- **Language spec:** `spec.md`  
- **Examples:** `examples/`  
- **Screenshots & demos:** `docs/`

---

## Developer tool: `chronos/lexer.py` (parse-tree printer)

`chronos/lexer.py` is a small utility that prints the parse tree for a `.chronos` file. It’s optional, but useful for grammar work and macro debugging.

```bash
python chronos/lexer.py examples/hello.chronos
```

---

## Limitations (honest notes)

- **Threads aren’t time‑traveled.** `go` uses Python threads; thread scheduling is not deterministic and cannot be rewound by the Timeline.
- **Time travel is about temporal state.** Rewinding time changes which temporal value you *query*; it does not undo I/O or external effects.
- **Macros are compile‑time.** They expand before runtime execution; the GUI won’t “show macros happening”.

---

## Milestones (development history)

ChronosLang evolved through a structured plan. Major milestones:

- **Interpreter core** — parsing + execution of basic programs (`hello.chronos`).
- **Static typing** — conservative checker with a permissive demo mode.
- **Concurrency** — `go` + rendezvous channels (`make(chan ...)`, `ch <- v`, `<-ch`).
- **Tests & runner** — `test "name":` blocks and CLI test execution.
- **Temporal engine** — `temporal` variables, scheduled writes (`@ t+...`), timeline control.
- **Probabilistic module** — `prob.*` distributions and simple inference demos.
- **ML module** — `ml.*` tensor helpers and linear regression demo (optional PyTorch backend).
- **Desktop GUI** — PyQt5 time‑travel debugger for visual timeline scrubbing.
- **Macros & reflection** — compile‑time expansion + runtime introspection APIs.
- **Web runner** — FastAPI backend with a browser UI for interactive runs.
- **Docs & packaging** — screenshots, recordings, spec, and paper artifacts.


## Contributing

Bug reports and documentation improvements are welcome. Please open an issue with:
- the failing `.chronos` snippet (or file),
- the command you ran,
- stdout/stderr output.

---

## License

MIT License © 2025 Srijon Kumar Shill  
See `LICENSE` for details.

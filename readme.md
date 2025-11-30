###### What is ChronosLang?

ChronosLang: A Python-inspired research programming language with native time-travel debugging, 
supporting temporal variables, scheduled assignments, deterministic concurrency, compile-time macros, 
runtime reflection, static typing, built-in testing, and lightweight probabilistic/ML modules and 
scientific computing.

ChronosLang is a small, research-first language that centers on three interlocking capabilities:

● Time-native programming. temporal variables hold histories; assignments may be scheduled (e.g., x = 5 @ 
  t+2s); a Timeline records events so tools can scrub, replay, and inspect past and future logical states.

● Lightweight deterministic concurrency. go for background execution plus unbuffered rendezvous channels 
  (make(chan) / ch <- v / <- ch) provide deterministic concurrency suitable for demos and teaching.

● Compile-time macros & runtime reflection. Hygienic-ish macros allow AST-level compile transforms; 
  reflect.* APIs let running programs inspect globals, functions, and temporal timelines.


 

**Highlights & goals**

● Run example program with functions, arithmetic, and print statements.

● Static typing / type inference 

● Deterministic goroutine-style concurrency and rendezvous channels

● Built-in testing & package runner

● Temporal variables and scheduled assignments

● Lightweight probabilistic (prob.*) and ML (ml.*) modules for demo examples

● Time-travel debugging & deterministic replay

● Compile-time macros (expanded before runtime) and a small reflection API

● Tools: CLI runner, FastAPI web-runner, PyQt5 time-travel debugger GUI prototype




**Quickstart (run & test)**

Clone:

git clone https://github.com/Srijon25/ChronosLang.git
cd ChronosLang


Create venv and activate:

python -m venv venv
# mac / linux
source venv/bin/activate
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

pip install lark-parser numpy

pip install pyqt5 fastapi uvicorn torch


Run examples:

# run the default example
python chronos/interpreter.py (examples/hello.chronos)

# run with static typing 
python chronos/interpreter.py examples/type_system.chronos

# run producer/consumer demo
python chronos/interpreter.py examples/producer_consumer.chronos

# run temporal demo and enter CLI time-travel REPL
python chronos/interpreter.py examples/temporal_demo.chronos --time-travel

# dump expanded AST after macros
python chronos/interpreter.py examples/macros_reflection.chronos --dump-expanded

# Run other example files
python chronos/interpreter.py examples/ .chronos

GUI prototype:

python chronos_time_travel_gui.py


Web runner (browser UI):

uvicorn server.app:app --reload --port 8000
# open http://127.0.0.1:8000




##### Demo Recordings

#### Time-Travel Debugging GUI
Watch the PyQt5 GUI in action: visualize timelines, step forward/backward, and inspect program state 
dynamically.

![Time-Travel Debugger Recording](docs/recordings/python_chronos_time_travel_debugger_gui.mp4)

#### Web Runner
Write, edit, and execute ChronosLang code directly in the browser using the FastAPI web runner, and run 
example programs interactively.

![ChronosLang Web Runner Full Recording](docs/recordings/ChronosLang_web_runner_full_demo.chronos.mp4)




**Primary commands and flags:**

run <file> — parse → macro-expand → (typecheck) → execute prelude.

test <path|dir> — run test "name": blocks in isolation.

build <path> — type-check-only (no execution).

**Flags:** 

--permissive — relax strict type checking (recommended for demos).

--skip-typecheck — skip static checking.

--time-travel — enter interactive time-travel REPL after prelude.

--trace <file> — record deterministic execution trace.

--replay <trace.json> — replay a previously recorded trace.

--dump-expanded — show AST/source after macro expansion.

Exit codes:

0 on success; non-zero for failures (e.g., failing tests or uncaught exceptions).




**Project layout**
ChronosLang/
├── chronos/
│   ├── interpreter.py
│   ├── lexer.py # Lark grammar and indentation -> braces preprocessor (same like interpreter) 
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
├── docs
│    ├──recordings
│    │     ├── ChronosLang_web_runner_full_demo.chronos.mp4
│    │     ├── python_chronos_time_travel_debugger_gui.mp4
│    │
│    │──screenshots
│         ├── hello.chronos_output.png
│         ├── macros_reflection.chronos_output.png
│         ├── prob_coin.chronos_output.png
│         ├── producer_consumer.chronos_output.png
│         ├── temporal_demo.chronos_output.png
│         ├── tensor_linear_regression.chronos_output.png
│         ├── tests_and_packages.chronos_output.png
│         ├── type_system_chronos_permissive_mode_output.png
│         └── type_system_chronos_static_mode_output.png
│
│
├── server/
│   ├── app.py                # FastAPI server
├── web/                  # frontend files
│   ├── index.html   
├── chronos_time_travel_gui.py  #PyQt5 Time-Travel debugger GUI
│
├── CITATION.cff
├── LICENSE
├── README.md
└── spec.md




**📖 Documentation**

Full language specification: spec.md

Screenshots & demos: docs/

Example programs: examples/




**🧪 Examples & Core Features**

# Run included programs from examples/:

  ● Hello & Functions — hello.chronos, type_system.chronos

    ► Basic functions, expressions, type inference (add(), auto_add()).

  ● Concurrency — producer_consumer.chronos

    ► go routines and channels (<-, ch <- value).

    ► Deterministic concurrency examples.

  ● Unit Tests & Package System — tests_and_packages.chronos

    ► Define test "<name>": blocks with assert statements.

    ► Run tests using chronos test examples/.

    ► Mixes code and tests in the same file; supports reproducible test-driven demos.  

  ● Temporal Variables & Time-Travel — temporal_demo.chronos

    ► temporal variables, scheduled assignments (@ t+Ns).

    ► Scrub history with --time-travel.  

  ● Probabilistic Programming — prob_coin.chronos

    ► prob.* API, inference backends (importance sampling, MCMC).

  ● Machine Learning / Tensors — tensor_linear_regression.chronos

    ► Tensor ops, linear regression, MSE loss; optional PyTorch backend.

  ● Macros & Reflection — macros_reflection.chronos

    ► Compile-time macros (logging, type inspection).

    ► Runtime reflection (function names, locals, timeline, variable inspection).


# Time-Travel Debugging GUI

● Visualize timeline and temporal variable histories.

● Step forward/backward through execution.

● Inspect and modify agent/program state dynamically.

✅ Scrubs temporal histories | ❌ Cannot display macro expansions or reflection inside 
active function calls.


**Best Practices**

● Define macros at the top of files.

● Use --permissive in demos to avoid runtime type errors.




**The lexer.py utility (diagnostic parse-tree printer)**

chronos/lexer.py is a small developer-facing utility meant for grammar/debugging work. 
Important facts:

It is not required to run examples. All example programs run with the interpreter 
(chronos.interpreter) 
without using lexer.py.

Purpose: it shares the same grammar and preprocessor as the interpreter and prints the 
parse for a .
chronos source file. This is helpful when developing grammar changes, writing macros, 
or 
teaching how the language is parsed.

Typical uses:

# print parse tree
python chronos/lexer.py examples/ .chronos




**🗺️ Roadmap**

ChronosLang has evolved through a structured, research-first development plan. Key 
milestones include:

Week 1 – Interpreter Core

  ● Added ChronosLang interpreter capable of running example files with functions, 
    arithmetic, and print statements.

  ● Introduced a lexer and hello.chronos for parse tree output.

Week 2 – Static Type System

  ● Integrated a static type system with type inference.

  ● Permissive mode enabled for testing.

  ● Added examples/type_system.chronos demonstrating type annotations and inferred 
    expressions.

Week 3 – Deterministic Concurrency

  ● Added goroutine-style concurrency with go, make(chan), and synchronous send/
    receive (<-, ch <- value).

  ● Added examples/producer_consumer.chronos.

Week 4 – Unit Testing & Package System

  ● Implemented package manager skeleton (chronos run/test/build).

  ● Integrated unit test syntax and added examples/tests_and_packages.chronos with 
    test "<name>": blocks and assertions.

Week 5 – Temporal Engine & Time-Travel Debugger

  ● Implemented ChronosEngine supporting temporal variables and scheduled assignments 
    (x = 5 @ t+2s).

  ● Added examples/temporal_demo.chronos showcasing reversible state and timeline 
    scrubbing.

Week 6 – Probabilistic Programming

  ● Added prob.* module with Bernoulli, Uniform, Normal distributions.

  ● Implemented simple inference backend (MCMC/importance sampling).

  ● Added Bayesian coin-flip demo: examples/prob_coin.chronos.

Week 7 – Machine Learning Core

  ● Introduced tensor type with autodiff.

  ● Integrated optional PyTorch backend.

  ● Added linear regression demo: examples/tensor_linear_regression.chronos.

Week 8 – Time-Travel Debugger GUI

  ● Added PyQt5 GUI for timeline visualization.

  ● Step forward/backward through program execution, inspect, and modify variable 
    states.

Week 9 – Macros & Reflection

  ● Implemented compile-time macros (log, debug_type, list_globals).

  ● Added runtime reflection API.

  ● Added examples/macros_reflection.chronos.

Week 10 – Web Runner

  ● Added browser-based UI with FastAPI backend to run ChronosLang examples 
    interactively.

Week 11 – Documentation & Specification

  ● Published official ChronosLang specification and documentation site with curated 
    screenshots.

Week 12 – Final Release & Publication

  ● Prepare README.md for public release.
  ● Publish the project on Zenodo to obtain a DOI.
  ● Submit the full software package and documentation to JORS.




🤝 Contributing

Contributions, bug reports, and feature requests are welcome.
Please check the roadmap before submitting a pull request (PR).




📜 License

MIT License © 2025 Srijon Kumar Shill  



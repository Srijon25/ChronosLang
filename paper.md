---
title: "ChronosLang: A Python-inspired research programming language with native time-travel debugging, temporal variables, deterministic concurrency, compile-time macros, runtime reflection, static typing, built-in testing, and lightweight probabilistic/ML modules"
tags:
  - programming-language
  - ChronosLang
  - time-travel debugging
  - temporal variables
  - deterministic concurrency
  - probabilistic programming
  - machine learning
  - Python-inspired language
  - static typing
  - macros
  - reflection
  - PyQt5 GUI
  - FastAPI web runner
authors:
  - family-names: Kumar Shill
    given-names: Srijon
    orcid: "https://orcid.org/0009-0001-6647-2743"
    affiliation: 1
affiliations:
  - name: Independent Researcher, Bangladesh
    index: 1
date: 2025-12-08
bibliography: paper.bib
---

# Summary

ChronosLang is a compact, Python-inspired research programming language [@VanRossum2009] and interpreter that makes **time, concurrency, and introspection** first-class concepts. It is built around three interlocking capabilities:

1. **Time-native programming.** Variables may be declared as *temporal* and carry a time-indexed history; assignments can be scheduled into the future (e.g., `x = 5 @ t+2s`), and a global `Timeline` object allows programs and tools to move forward and backward in logical time. Temporal variables integrate tightly with a command-line time-travel REPL and a PyQt5 debugging GUI [@PyQt5Guide2023].

2. **Deterministic concurrency.** ChronosLang provides lightweight concurrency inspired by communicating sequential processes [@Hoare1978] and Go-style channel-based communication [@Pike2012]. Goroutines (`go expr`) and unbuffered rendezvous channels (`make(chan T)`, `<-ch`, `ch <- v`) allow deterministic concurrent examples suitable for teaching and research.

3. **Compile-time macros and runtime reflection.** Hygienic-ish macros allow AST-level compile-time transformations, and a reflection API enables programs to inspect globals, locals, types, and temporal state. The design follows classic metaobject protocol ideas [@Kiczales1991] while remaining small and inspectable.

ChronosLang further includes:

- a conservative **static type checker** inspired by foundational type theory [@Milner1978],
- a built-in **unit test framework** aligned with test-driven development principles [@Beck2003],
- a lightweight **probabilistic module** inspired by Church-style generative models [@Goodman2008],
- a **tensor/ML module** for small machine learning experiments related to modern deep learning concepts [@LeCun2015],
- and two complementary tools: a **PyQt5 time-travel debugger GUI** and a **FastAPI-based web runner**.

ChronosLang is open source (MIT license), intentionally compact, and designed for use as a **teaching language** and **research testbed** for temporal semantics, reversible debugging, concurrency, and language-integrated probabilistic/ML features.

# Statement of need

Students and researchers in programming languages, debugging, distributed systems, and AI often need **small, self-contained languages** that are easy to read, modify, and extend. Existing tools address only parts of this need:

- General-purpose languages (e.g., Python or Julia) and libraries like NumPy or PyTorch support scientific computing and ML but do not treat **temporal semantics** or **timeline scrubbing** as first-class constructs [@VanRossum2009; @LeCun2015].
- Time-travel debuggers and record/replay systems exist in some IDEs or low-level tools, but they are tightly coupled to specific runtimes and do not expose a **language-level temporal model** [@Zeller2002; @Berger2006].
- Probabilistic programming frameworks such as Church [@Goodman2008] or Pyro embed probabilistic models into existing host languages rather than offering a compact language where probabilistic and temporal constructs coexist.

As a result, instructors and researchers who want to demonstrate *temporal semantics*, *deterministic concurrency*, *macro systems*, and *probabilistic/ML reasoning* often require multiple unrelated tools, each with different runtime models and debugging capabilities. This increases cognitive overhead and obscures core ideas.

ChronosLang provides a unified solution:

- temporal variables and time-travel debugging are **part of the language**,  
- concurrency uses a small, deterministic rendezvous-channel model [@Hoare1978; @Pike2012],  
- macros and reflection are accessible and inspectable [@Kiczales1991],  
- probabilistic and ML modules are built into the language [@Goodman2008; @LeCun2015],  
- and the entire interpreter is small enough for students to understand and extend.

ChronosLang is ideal for:

- **Educators** teaching interpreters, type systems, debugging, concurrency, or probabilistic reasoning.  
- **Students** exploring implementation techniques by modifying a real interpreter.  
- **Researchers** prototyping new semantic or debugging ideas quickly.

# System overview

ChronosLang’s implementation is organized as a **five-phase pipeline** and a set of compact runtime abstractions.

## Front end: syntax, preprocessor, and parser

ChronosLang uses indentation-based syntax similar to Python [@VanRossum2009]. A minimal **indentation-to-braces preprocessor** inserts `{}` markers based on indentation levels, simplifying grammar design. The resulting token stream is parsed by a **Lark** LALR parser into an AST supporting:

- function definitions and calls,  
- temporal variables and scheduled assignments (`@ t+NUMBER s`),  
- goroutines (`go expr`) and channel operations (`<-ch`, `ch <- v`),  
- macros, test blocks, lists, and type expressions.

This front end is intentionally compact and easily extended.

## Static type checking

The `TypeChecker` supports base types (`int`, `float`, `string`, `auto`), list types, and channel types. It performs return-type inference and verifies signature consistency based on classical type theory concepts [@Milner1978]. ChronosLang supports both:

- **strict mode**, where type errors abort execution,  
- **permissive mode**, allowing coercions useful for notebooks or demos.

## Runtime environment and temporal semantics

ChronosLang provides:

- lexical environments with parent scopes,  
- first-class `Function` objects,  
- a `Timeline` that stores scheduled events,  
- `TemporalVar` objects that maintain timestamped histories.

Advancing time applies scheduled events, and temporal reads return values from the compact event history. Time may be moved backward without undoing state—values are accessed by querying history at earlier times.

This allows **reversible debugging** without requiring reverse execution, inspired by prior work on replay and debugging systems [@Zeller2002; @Berger2006].

## Concurrency model

ChronosLang implements Go-style concurrency with CSP roots [@Hoare1978]:

- `go expr` spawns a thread,  
- channels implement synchronous rendezvous (`send` waits for `recv`),  
- interactions are deterministic and reproducible.

This makes concurrent examples easy to teach and reason about.

## Macros and compile-time behavior

Compile-time macros allow AST-level transformations before execution, following the philosophy of metaobject protocols [@Kiczales1991] but in a simpler form. Macros are expanded before runtime and do not appear in temporal histories or the time-travel debugger.

## Reflection

The global `reflect` object provides:

- inspection of globals, locals, and types,  
- inspection of temporal histories,  
- access to function metadata,  
- a list of compile-time macros,  
- a helper `reflect_func_name()` for introspection.

This makes ChronosLang an excellent environment for studying language reflection and program structure.

## Probabilistic and ML modules

### `prob` module  
Offers:

- uniform, normal, and Bernoulli distributions,  
- binomial likelihood terms,  
- inference via importance sampling or Metropolis–Hastings [@Goodman2008].

### `ml` and `tensor` modules  
Support:

- tensor construction,  
- matrix operations,  
- MSE loss,  
- linear regression training using PyTorch or NumPy,  
- connections to modern ML techniques [@LeCun2015].

## Tooling: CLI, web runner, GUI debugger

ChronosLang includes:

- a **CLI runner**,  
- a **FastAPI web runner** inspired by lightweight frameworks [@Grinberg2018],  
- a **PyQt5 GUI time-travel debugger** [@PyQt5Guide2023] for inspecting temporal histories interactively.

# Illustrative examples

The repository includes examples demonstrating:

- functions and arithmetic,  
- typed and inferred functions,  
- concurrency with channels,  
- test blocks and assertions,  
- temporal variables and scheduled updates,  
- probabilistic inference with binomial data,  
- ML linear regression,  
- compile-time macros and runtime reflection.

These examples support both teaching and research use.

# Impact and reuse

ChronosLang is not intended as a production language but as a **pedagogical and research platform** offering a compact, extensible environment for temporal semantics, debugging, concurrency, and probabilistic/ML features.

Its small codebase, extensive documentation (`spec.md`), and built-in tools make it well suited for:

- university courses,  
- research prototypes,  
- debugging/visualization studies,  
- experimentation with temporal or reflective semantics.

# Availability

ChronosLang is free and open source under the MIT License:

- Repository: https://github.com/Srijon25/ChronosLang  
- Documentation: `README.md`, `spec.md`  
- Examples: `examples/`  
- Recordings & screenshots: `docs/`

# Acknowledgements

ChronosLang builds on the Python ecosystem, including Lark, NumPy, PyTorch, FastAPI, Uvicorn, and PyQt5.

# References

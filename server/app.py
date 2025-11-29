import os 
import sys 
import uuid
import shutil
import tempfile
import subprocess
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import traceback
import json
from fastapi import HTTPException

app = FastAPI(title="ChronosLang Runner (safe)")

# Serve a browser UI from /static
WEB_DIR = "web"
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR, html=True), name="static")

CHRONOS_INTERPRETER = os.path.abspath(os.path.join("chronos", "interpreter.py"))

# Use same Python as this process (works with virtualenv)
PYTHON = sys.executable

# Defaults
DEFAULT_TIMEOUT = 20  # seconds

def ensure_interpreter_exists():
    if not os.path.isfile(CHRONOS_INTERPRETER):
        raise FileNotFoundError(f"Chronos interpreter not found at: {CHRONOS_INTERPRETER}")

def run_interpreter_on_file(path: str, flags: Optional[list] = None, timeout: int = DEFAULT_TIMEOUT):
    """
    Run: <python> chronos/interpreter.py run <path> [flags...]
    Returns dict: {returncode, stdout, stderr, timed_out}
    """
    ensure_interpreter_exists()
    cmd = [PYTHON, CHRONOS_INTERPRETER, "run", path]
    if flags:
        cmd.extend(flags)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "timed_out": False, "cmd": " ".join(cmd)}
    except subprocess.TimeoutExpired as ex:
        return {
            "returncode": None,
            "stdout": ex.stdout or "",
            "stderr": (ex.stderr or "") + f"\n[Timed out after {timeout}s]",
            "timed_out": True,
            "cmd": " ".join(cmd),
        }
    except FileNotFoundError as ex:
        return {"returncode": None, "stdout": "", "stderr": str(ex), "timed_out": False, "cmd": " ".join(cmd)}

try:
    from chronos.interpreter import Interpreter, Environment, TemporalVar, Function  # type: ignore
    IN_PROCESS_AVAILABLE = True
except Exception:
    Interpreter = None
    Environment = None
    TemporalVar = None
    Function = None
    IN_PROCESS_AVAILABLE = False

# In-memory sessions store: session_id -> {"interp": Interpreter, "env": module_env}
SESSIONS = {}

def _prepare_session_from_code_inproc(code: str, permissive: bool = False, infer: bool = False):
    """
    Create an in-process Interpreter instance, parse/expand macros, typecheck (optional),
    execute prelude nodes into a module environment and return (interp, module_env).
    """
    if not IN_PROCESS_AVAILABLE:
        raise RuntimeError("In-process chronos interpreter not available (chronos not importable)")

    interp = Interpreter()
    # preprocess then parse
    try:
        src2 = interp.preprocess_indent(code)
    except Exception:
        # fallback if preprocess_indent isn't on Interpreter
        from chronos import interpreter as chronos_mod  # type: ignore
        src2 = chronos_mod.preprocess_indent(code)

    tree = interp.parser.parse(src2)

    # macros
    interp.collect_macros(tree)
    interp.macro_expand_top_level(tree)

    # optional typecheck (mirror subprocess behavior: permissive means skip type errors)
    if not permissive:
        try:
            from chronos.interpreter import TypeChecker  # type: ignore
            checker = TypeChecker(tree, infer=infer)
            checker.check()
            if infer:
                interp.type_info = checker.functions
        except Exception:
            # raise so caller sees type errors (consistent with run behavior)
            raise

    # partition and execute prelude
    prelude_nodes = []
    for node in tree.children:
        if isinstance(node, type(tree)) and getattr(node, "data", None) == "test_def":
            # ignore tests in prelude
            continue
        prelude_nodes.append(node)

    module_env = Environment()
    module_env.vars.update(interp.global_env.vars)

    for node in prelude_nodes:
        interp.exec_stmt(node, module_env, permissive)

    return interp, module_env

# --- minimal HTTP endpoints for time-travel sessions ---

@app.post("/start_session")
async def start_session(request: Request):
    """
    JSON body: { code: str, permissive: bool (optional), infer: bool (optional) }
    Returns { ok, session_id, times, globals }
    """
    if not IN_PROCESS_AVAILABLE:
        return JSONResponse({"ok": False, "error": "In-process interpreter not available"}, status_code=501)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    code = data.get("code", "")
    if not code:
        return JSONResponse({"ok": False, "error": "no code provided"}, status_code=400)
    permissive = bool(data.get("permissive", False))
    infer = bool(data.get("infer", False))
    try:
        interp, module_env = _prepare_session_from_code_inproc(code, permissive=permissive, infer=infer)
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse({"ok": False, "error": str(e), "traceback": tb}, status_code=400)

    sid = uuid.uuid4().hex
    SESSIONS[sid] = {"interp": interp, "env": module_env}
    times = interp.timeline.times()
    globals_preview = list(module_env.vars.keys())[:200]
    return {"ok": True, "session_id": sid, "times": times, "globals": globals_preview}

@app.post("/step_forward")
async def step_forward(session_id: str = Form(...), seconds: float = Form(...)):
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    interp = s["interp"]
    env = s["env"]
    try:
        interp.timeline.step_forward(float(seconds), env)
        return {"ok": True, "current_time": interp.timeline.current_time, "times": interp.timeline.times()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/step_back")
async def step_back(session_id: str = Form(...), seconds: float = Form(...)):
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    interp = s["interp"]
    interp.timeline.step_backward(float(seconds))
    return {"ok": True, "current_time": interp.timeline.current_time}

@app.get("/show")
async def show_var(session_id: str, name: str):
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    interp = s["interp"]
    env = s["env"]
    try:
        val = env.get(name)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if TemporalVar is not None and isinstance(val, TemporalVar):
        v = val.value_at(interp.timeline.current_time)
        return {"ok": True, "temporal": True, "value": v, "history": val.history()}
    if Function is not None and isinstance(val, Function):
        return {"ok": True, "type": "function", "params": val.params, "return_type": val.return_type}
    try:
        json.dumps(val)
        return {"ok": True, "temporal": False, "value": val}
    except Exception:
        return {"ok": True, "temporal": False, "value": repr(val)}

@app.get("/history")
async def history_var(session_id: str, name: str):
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    env = s["env"]
    try:
        val = env.get(name)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if TemporalVar is not None and isinstance(val, TemporalVar):
        return {"ok": True, "history": val.history()}
    return {"ok": False, "error": "not a temporal variable"}

@app.get("/times")
async def times(session_id: str):
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    interp = s["interp"]
    return {"ok": True, "times": interp.timeline.times(), "current_time": interp.timeline.current_time}

@app.post("/call_function")
async def call_function(session_id: str = Form(...), func_name: str = Form(...), args: str = Form("[]")):
    if not IN_PROCESS_AVAILABLE:
        return JSONResponse({"ok": False, "error": "In-process interpreter not available"}, status_code=501)
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    interp = s["interp"]
    env = s["env"]
    try:
        func = env.get(func_name)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(func, Function):
        return {"ok": False, "error": f"'{func_name}' not a function"}
    try:
        arglist = json.loads(args)
    except Exception:
        arglist = []
    try:
        result = interp.call_function(func, arglist, permissive=True)
        try:
            json.dumps(result)
            return {"ok": True, "result": result}
        except Exception:
            return {"ok": True, "result": repr(result)}
    except Exception as e:
        tb = traceback.format_exc()
        return {"ok": False, "error": str(e), "traceback": tb}

@app.post("/end_session")
async def end_session(session_id: str = Form(...)):
    if session_id in SESSIONS:
        del SESSIONS[session_id]
    return {"ok": True}

@app.post("/run")
async def run_code(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        code = body.get("code", "")
        filename = body.get("filename", f"tmp_{uuid.uuid4().hex}.chronos")
        flags = body.get("flags") or []
    else:
        form = await request.form()
        code = form.get("code", "")
        filename = form.get("filename", f"tmp_{uuid.uuid4().hex}.chronos")
        flags_raw = form.get("flags", "")
        if isinstance(flags_raw, str) and flags_raw.strip():
            flags = [f.strip() for f in flags_raw.split(",") if f.strip()]
        else:
            flags = []

    if not filename.endswith(".chronos"):
        filename = filename + ".chronos"

    tmpdir = tempfile.mkdtemp(prefix="chronos-run-")
    try:
        path = os.path.join(tmpdir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)

        res = run_interpreter_on_file(path, flags=flags, timeout=DEFAULT_TIMEOUT)
        return JSONResponse(res)
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

@app.post("/upload")
async def upload_and_run(file: UploadFile = File(...), flags: Optional[str] = Form(None)):
    tmpdir = tempfile.mkdtemp(prefix="chronos-upload-")
    try:
        save_path = os.path.join(tmpdir, file.filename)
        with open(save_path, "wb") as out:
            contents = await file.read()
            out.write(contents)

        flags_list = []
        if flags:
            flags_list = [f.strip() for f in flags.split(",") if f.strip()]

        res = run_interpreter_on_file(save_path, flags=flags_list, timeout=DEFAULT_TIMEOUT)
        return JSONResponse(res)
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

# --- NEW /run_test endpoint ---
@app.post("/run_test")
async def run_test(file: UploadFile = File(...)):
    tmpdir = tempfile.mkdtemp(prefix="chronos-test-")
    try:
        save_path = os.path.join(tmpdir, file.filename)
        with open(save_path, "wb") as out:
            contents = await file.read()
            out.write(contents)

        cmd = [PYTHON, CHRONOS_INTERPRETER, "test", save_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)

        return JSONResponse({
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "cmd": " ".join(cmd)
        })
    except subprocess.TimeoutExpired as ex:
        return JSONResponse({
            "returncode": None,
            "stdout": ex.stdout or "",
            "stderr": (ex.stderr or "") + f"\n[Timed out after {DEFAULT_TIMEOUT}s]",
            "timed_out": True,
            "cmd": " ".join(cmd)
        })
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

@app.get("/health")
async def health():
    return {"status": "ok", "python": PYTHON, "interpreter_exists": os.path.isfile(CHRONOS_INTERPRETER)}

@app.get("/", response_class=HTMLResponse)
async def root_index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(
        """
        <html><body>
          <h2>ChronosLang Runner</h2>
          <p>Place a web/index.html in ./web to use the browser UI, or POST /run and /upload.</p>
        </body></html>
        """
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)

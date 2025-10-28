"""
ChronosLang Time-Travel Debugger (PyQt5 prototype)
Requirements: PyQt5, chronos/interpreter.py must be importable as `chronos.interpreter`
Usage:
    python chronos_time_travel_gui.py [path/to/program.chronos]
"""

import sys
import os
import traceback
from functools import partial

from PyQt5 import QtWidgets, QtCore, QtGui

# The GUI relies on Interpreter, Timeline, Environment, TemporalVar from your interpreter.
try:
    # if your interpreter is a package chronos with interpreter.py inside
    from chronos.interpreter import Interpreter, Timeline, Environment, TemporalVar, preprocess_indent, Lark, Tree, Token
except Exception:
    # fallback: try importing interpreter.py from current directory
    try:
        import chronos.interpreter as interp_mod
        Interpreter = interp_mod.Interpreter
        Timeline = interp_mod.Timeline
        Environment = interp_mod.Environment
        TemporalVar = interp_mod.TemporalVar
        preprocess_indent = interp_mod.preprocess_indent
    except Exception as e:
        print("Failed to import your Interpreter. Please ensure chronos/interpreter.py is on PYTHONPATH.")
        print(e)
        sys.exit(1)


def safe_repr(obj):
    """Try to produce a short safe representation."""
    try:
        # Torch tensors may not be available — guard with hasattr
        if hasattr(obj, "tolist"):
            # convert small arrays/tensors to lists for nicer display, but don't explode on huge objects
            try:
                lst = obj.tolist()
                return repr(lst)
            except Exception:
                pass
        return repr(obj)
    except Exception:
        return f"<unreprable {type(obj).__name__}>"


class ChronosDebugger(QtWidgets.QWidget):
    def __init__(self, chronos_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ChronosLang Time-Travel Debugger (Week 8 Prototype)")
        self.resize(900, 600)

        self.interp = Interpreter()
        self.module_env = None
        self.prelude_nodes = None
        self.program_src = None

        # GUI widgets
        self.open_btn = QtWidgets.QPushButton("Open .chronos")
        self.run_btn = QtWidgets.QPushButton("Run Prelude")
        self.play_btn = QtWidgets.QPushButton("Play")
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.step_forward_btn = QtWidgets.QPushButton("Step →")
        self.step_back_btn = QtWidgets.QPushButton("← Step")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setEnabled(False)
        self.time_label = QtWidgets.QLabel("time: 0.0s")

        # variables list and details
        self.var_list = QtWidgets.QListWidget()
        self.var_detail = QtWidgets.QPlainTextEdit()
        self.var_detail.setReadOnly(True)

        # timeline quick jumps
        self.times_combo = QtWidgets.QComboBox()
        self.times_combo.addItem("No scheduled times")
        self.times_combo.setEnabled(False)

        # layout
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.addWidget(self.open_btn)
        top_bar.addWidget(self.run_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.step_back_btn)
        top_bar.addWidget(self.play_btn)
        top_bar.addWidget(self.pause_btn)
        top_bar.addWidget(self.step_forward_btn)
        top_bar.addStretch()
        top_bar.addWidget(self.time_label)

        mid = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_vbox = QtWidgets.QVBoxLayout()
        left_widget = QtWidgets.QWidget()
        left_vbox.addWidget(QtWidgets.QLabel("Variables"))
        left_vbox.addWidget(self.var_list)
        left_vbox.addWidget(QtWidgets.QLabel("Scheduled times"))
        left_vbox.addWidget(self.times_combo)
        left_widget.setLayout(left_vbox)

        right_vbox = QtWidgets.QVBoxLayout()
        right_widget = QtWidgets.QWidget()
        right_vbox.addWidget(QtWidgets.QLabel("Value / History (selected var)"))
        right_vbox.addWidget(self.var_detail)
        right_widget.setLayout(right_vbox)

        mid.addWidget(left_widget)
        mid.addWidget(right_widget)

        main_v = QtWidgets.QVBoxLayout(self)
        main_v.addLayout(top_bar)
        main_v.addWidget(self.slider)
        main_v.addWidget(mid)

        # signals
        self.open_btn.clicked.connect(self.open_file_dialog)
        self.run_btn.clicked.connect(self.run_prelude)
        self.play_btn.clicked.connect(self.on_play)
        self.pause_btn.clicked.connect(self.on_pause)
        self.step_forward_btn.clicked.connect(partial(self.step_time, +1.0))
        self.step_back_btn.clicked.connect(partial(self.step_time, -1.0))
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.var_list.currentItemChanged.connect(self.on_var_selected)
        self.times_combo.currentIndexChanged.connect(self.on_times_combo_changed)

        # playback timer
        self.timer = QtCore.QTimer()
        self.timer.setInterval(200)  # ms
        self.timer.timeout.connect(partial(self._play_tick, 0.2))  # advance 0.2s per tick

        if chronos_path:
            self.load_file(chronos_path)


    #  file/load/run helpers 

    def open_file_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Chronos file", ".", "Chronos files (*.chronos);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to read file: {e}")
            return
        self.program_src = src
        self.setWindowTitle(f"Chronos Debugger — {os.path.basename(path)}")
        self.run_btn.setEnabled(True)
        self.slider.setEnabled(False)
        self.var_list.clear()
        self.var_detail.clear()
        self.times_combo.clear()
        self.times_combo.addItem("No scheduled times")
        self.times_combo.setEnabled(False)
        self.module_env = None

    def run_prelude(self):
        if not self.program_src:
            return
        try:
            src2 = preprocess_indent(self.program_src)
            tree = self.interp.parser.parse(src2)
            # optional: typecheck before running
            tc = None
            try:
                from chronos.interpreter import TypeChecker as _TC
                tc = _TC(tree)
                tc.check()
            except Exception:
                # ignore static type errors for GUI demo; still continue
                pass

            # partition nodes (prelude)
            prelude_nodes = []
            for node in tree.children:
                if isinstance(node, Tree) and node.data == "test_def":
                    continue
                prelude_nodes.append(node)
            self.prelude_nodes = prelude_nodes

            # prepare module env as in interpreter.run
            module_env = Environment()
            module_env.vars.update(self.interp.global_env.vars)

            # Execute prelude nodes (this will schedule events in interpreter.timeline)
            for node in prelude_nodes:
                self.interp.exec_stmt(node, module_env, permissive=True)

            self.module_env = module_env

            # Setup slider range from 0 to max scheduled time (or 10s default)
            times = sorted(self.interp.timeline.times())
            if times:
                max_t = max(times) if times else 1.0
            else:
                max_t = max(1.0, self.interp.timeline.current_time)
            # keep a small padding
            self._min_time = 0.0
            self._max_time = float(max_t) + 0.0001
            self._slider_steps = 1000  # resolution
            self.slider.setMinimum(0)
            self.slider.setMaximum(self._slider_steps)
            self.slider.setValue(0)
            self.slider.setEnabled(True)
            self.times_combo.clear()
            for t in times:
                self.times_combo.addItem(f"{t:.4f}s", userData=float(t))
            if times:
                self.times_combo.setEnabled(True)
            self.update_var_list()
            self.update_time_label()
            QtWidgets.QMessageBox.information(self, "Run Prelude", "Prelude executed. Timeline ready to scrub.")
        except Exception as e:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Error during run", f"{type(e).__name__}: {e}")


    #  time control 

    def _slider_to_time(self, val):
        frac = val / float(self._slider_steps) if self._slider_steps else 0.0
        return self._min_time + frac * (self._max_time - self._min_time)

    def _time_to_slider(self, t):
        if self._max_time == self._min_time:
            return 0
        frac = (t - self._min_time) / (self._max_time - self._min_time)
        return int(round(frac * self._slider_steps))

    def on_slider_changed(self, value):
        if self.module_env is None:
            return
        t = self._slider_to_time(value)
        # move interpreter timeline to t and apply events
        try:
            self.interp.timeline.run_to(t, self.module_env)
        except Exception as e:
            # timeline.run_to may raise for weird inputs — ignore for GUI demo
            print("timeline.run_to error:", e)
        self.update_time_label()
        self.update_var_list(update_selection=True)

    def update_time_label(self):
        t = self.interp.timeline.current_time
        self.time_label.setText(f"time: {t:.4f}s")

    def step_time(self, seconds):
        if self.module_env is None:
            return
        newt = max(0.0, self.interp.timeline.current_time + float(seconds))
        if newt > self._max_time:
            newt = self._max_time
        self.interp.timeline.run_to(newt, self.module_env)
        self.slider.blockSignals(True)
        self.slider.setValue(self._time_to_slider(newt))
        self.slider.blockSignals(False)
        self.update_time_label()
        self.update_var_list(update_selection=True)

    def on_play(self):
        if self.module_env is None:
            return
        self.timer.start()

    def on_pause(self):
        self.timer.stop()

    def _play_tick(self, step_seconds):
        # advance forward by step_seconds
        if self.module_env is None:
            return
        cur = self.interp.timeline.current_time
        nxt = min(self._max_time, cur + step_seconds)
        self.interp.timeline.run_to(nxt, self.module_env)
        self.slider.blockSignals(True)
        self.slider.setValue(self._time_to_slider(nxt))
        self.slider.blockSignals(False)
        self.update_time_label()
        self.update_var_list(update_selection=True)
        if nxt >= self._max_time:
            self.timer.stop()


    #  variables UI 

    def update_var_list(self, update_selection=False):
        if self.module_env is None:
            return
        # show top-level variables (exclude builtins/functions)
        self.var_list.clear()
        for name, val in sorted(self.module_env.vars.items()):
            # omit internal builtins if desired
            if name.startswith("_"):
                continue
            # label temporal vs static
            if isinstance(val, TemporalVar):
                display = f"{name}  (temporal)"
            else:
                display = name
            item = QtWidgets.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, name)
            self.var_list.addItem(item)
        if update_selection:
            # keep selection if possible
            items = self.var_list.findItems("*", QtCore.Qt.MatchWrap | QtCore.Qt.MatchWildcard)
            if items:
                self.var_list.setCurrentRow(0)

    def on_var_selected(self, current, previous):
        if not current or self.module_env is None:
            self.var_detail.clear()
            return
        name = current.data(QtCore.Qt.UserRole)
        try:
            val = self.module_env.get(name)
        except Exception:
            self.var_detail.setPlainText(f"Name '{name}' not found")
            return
        # If temporal variable, show current value and history
        out_lines = []
        if isinstance(val, TemporalVar):
            tnow = self.interp.timeline.current_time
            cur = val.value_at(tnow)
            out_lines.append(f"{name} (temporal) at t={tnow:.4f}:")
            out_lines.append(safe_repr(cur))
            out_lines.append("")
            out_lines.append("History (time: value):")
            for tt, vv in val.history():
                out_lines.append(f"{tt:.4f}: {safe_repr(vv)}")
        else:
            out_lines.append(f"{name} ({type(val).__name__}):")
            out_lines.append(safe_repr(val))
        self.var_detail.setPlainText("\n".join(out_lines))

    def on_times_combo_changed(self, idx):
        if idx <= 0 or self.module_env is None:
            return
        t = self.times_combo.currentData()
        if t is None:
            return
        self.interp.timeline.run_to(float(t), self.module_env)
        self.slider.blockSignals(True)
        self.slider.setValue(self._time_to_slider(float(t)))
        self.slider.blockSignals(False)
        self.update_time_label()
        self.update_var_list(update_selection=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    dbg = ChronosDebugger(path)
    dbg.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main() 

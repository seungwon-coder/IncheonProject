"""Verify that the panel reports a stopped process only after wait() confirms it."""
import sys
import types

sys.modules["requests"] = types.SimpleNamespace(ConnectionError=ConnectionError)
import control_panel


class Process:
    pid = 1234
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert timeout == 12
        self.returncode = 0


def verify(pid):
    panel = control_panel.Panel.__new__(control_panel.Panel)
    panel.core_process = Process()
    panel.local_base = lambda: "http://127.0.0.1:5000"
    calls = []
    panel.http = lambda url, payload=None, timeout=5: (
        calls.append(url) or ({"pid": pid} if url.endswith("/health") else {"accepted": True})
    )
    shown = []
    control_panel.messagebox.askyesno = lambda *args: True
    control_panel.messagebox.showinfo = lambda *args: shown.append(args[-1])
    panel.opc_status = type("Var", (), {"set": lambda self, text: shown.append(text)})()
    errors = []
    def run_job(title, work, done):
        try:done(work())
        except Exception as exc:errors.append(str(exc))
    panel.run_job = run_job
    panel.stop_core()
    return calls, shown, errors


calls, shown, errors = verify(1234)
assert len(calls) == 2 and calls[1].endswith("/api/core/stop")
assert not errors and any("종료 완료" in text for text in shown)
calls, shown, errors = verify(9999)
assert len(calls) == 1 and not shown and "PID 9999" in errors[0]
print("PASS: owned PID exit confirmed; mismatched PID not stopped")

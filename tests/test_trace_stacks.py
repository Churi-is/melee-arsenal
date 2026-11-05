"""Call-stack pairing, including callbacks on an externally-created native thread."""
import ctypes
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk_mods"))
from re_probe.probe import trace  # noqa: E402


def func(path):
    return types.SimpleNamespace(func=types.SimpleNamespace(_path_name=lambda: path))


class TraceStackTests(unittest.TestCase):
    def test_interleaved_native_threads_and_nesting(self):
        tracer = trace.CallTracer(trace.TraceSettings(capture_args=False))
        a, b, c = (func(name) for name in ("A", "B", "C"))
        with patch.object(trace.threading, "get_ident") as ident:
            ident.return_value = 10
            tracer._pre(None, None, None, a)
            tracer._pre(None, None, None, b)
            ident.return_value = 20
            tracer._pre(None, None, None, c)
            tracer._post(None, None, None, c)
            ident.return_value = 10
            tracer._post(None, None, None, b)
            tracer._post(None, None, None, a)
        self.assertEqual(tracer._edges[("A", "B")].count, 1)
        self.assertEqual(tracer._edges[(None, "A")].count, 1)
        self.assertEqual(tracer._edges[(None, "C")].count, 1)
        self.assertEqual(tracer.status()["open_frames"], 0)
        self.assertEqual(tracer.stack_drift, 0)

    def test_mismatch_discards_stale_frames(self):
        tracer = trace.CallTracer(trace.TraceSettings(capture_args=False))
        tracer._pre(None, None, None, func("A"))
        tracer._post(None, None, None, func("B"))
        self.assertEqual(tracer.status()["discarded_frames"], 1)
        self.assertEqual(tracer.status()["open_frames"], 0)
        tracer._pre(None, None, None, func("C"))
        tracer._post(None, None, None, func("C"))
        self.assertEqual(tracer._edges[(None, "C")].count, 1)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc"), "requires Linux C compiler")
    def test_callbacks_from_foreign_native_thread(self):
        # CPython creates temporary thread states for independent ctypes callbacks
        # from a pthread. This replicates the suspected SDK callback lifecycle.
        source = r'''
#include <pthread.h>
typedef void (*cb)(int);
static void *run(void *f) { cb fn = (cb)f; fn(1); fn(2); fn(3); fn(4); return 0; }
void call_on_thread(cb fn) { pthread_t t; pthread_create(&t, 0, run, (void *)fn); pthread_join(t, 0); }
'''
        with tempfile.TemporaryDirectory() as tmp:
            source_path, lib_path = Path(tmp) / "thread.c", Path(tmp) / "thread.so"
            source_path.write_text(source)
            subprocess.run(["cc", "-shared", "-fPIC", "-pthread", str(source_path), "-o", str(lib_path)], check=True)
            lib = ctypes.CDLL(str(lib_path))
            callback_type = ctypes.CFUNCTYPE(None, ctypes.c_int)
            tracer = trace.CallTracer(trace.TraceSettings(capture_args=False))
            a, b = func("A"), func("B")

            @callback_type
            def callback(step):
                if step == 1:
                    tracer._pre(None, None, None, a)
                elif step == 2:
                    tracer._pre(None, None, None, b)
                elif step == 3:
                    tracer._post(None, None, None, b)
                else:
                    tracer._post(None, None, None, a)

            lib.call_on_thread.argtypes = [callback_type]
            lib.call_on_thread(callback)
            self.assertEqual(tracer.stack_drift, 0)
            self.assertEqual(tracer._edges[("A", "B")].count, 1)
            self.assertEqual(tracer._edges[(None, "A")].count, 1)
            self.assertEqual(tracer.status()["open_frames"], 0)


if __name__ == "__main__":
    unittest.main()

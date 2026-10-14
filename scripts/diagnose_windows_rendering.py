"""Bounded isolated Windows graphics diagnostics; no research images or test skips."""

import subprocess
import sys
import textwrap

CODE = textwrap.dedent("""
import faulthandler
faulthandler.enable()
import vtk
print("VTK", vtk.vtkVersion.GetVTKVersion(), flush=True)
if MODE == "vtk":
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
else:
    from PySide6.QtWidgets import QApplication
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
    app = QApplication([])
    widget = QVTKRenderWindowInteractor()
    window = widget.GetRenderWindow()
    if MODE == "shown":
        widget.show()
        app.processEvents()
    if MODE == "offscreen":
        window.SetOffScreenRendering(1)
print("window", window.GetClassName(), flush=True)
renderer = vtk.vtkRenderer()
window.AddRenderer(renderer)
print("before initialization", flush=True)
if MODE != "vtk":
    widget.Initialize()
print("before rendering", flush=True)
window.Render()
print("render complete", flush=True)
print("\\n".join(window.ReportCapabilities().splitlines()[:8]), flush=True)
window.Finalize()
""")

if __name__ == "__main__":
    for mode in ("vtk", "hidden", "shown", "offscreen"):
        print("Graphics probe:", mode, flush=True)
        try:
            result = subprocess.run(
                [sys.executable, "-u", "-c", CODE.replace("MODE", repr(mode))], timeout=30
            )
        except subprocess.TimeoutExpired:
            print("Probe timed out", flush=True)
        else:
            print("Probe return code:", result.returncode, flush=True)

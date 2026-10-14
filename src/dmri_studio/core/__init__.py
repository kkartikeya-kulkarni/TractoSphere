"""GUI-independent diffusion data objects and validation services.

Import public classes from their modules (``dataset``, ``gradients``,
``validation``, ``loading``). This initializer deliberately does not import
every service, which keeps dependencies and initialization explicit.
"""

# A Windows native-extension failure can terminate Python before a loader can
# catch an error. Set the narrow, process-local I/O policy before core reads;
# no service registry, GUI or processing engine is initialized here.
import sys

if sys.platform == "win32":
    from .image_io import configure_safe_image_io

    configure_safe_image_io()

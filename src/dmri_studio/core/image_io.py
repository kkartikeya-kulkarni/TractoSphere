"""Process-local NIfTI backend policy; never edits data or installed libraries.

Windows CI reproduced a native indexed_gzip 1.10.3 exit on Unicode filenames;
the same file passes with NiBabel's standard gzip reader. Disable that optional
accelerator on Windows before any core image read. macOS/Linux are unaffected.
"""

import platform


def configure_safe_image_io(system: str | None = None) -> bool:
    """Select standard gzip on Windows; return whether the policy was applied.

    NiBabel 5.3 dispatches through openers; 5.4 moved this optional-backend flag
    to _compression. Configure only process memory, preserving gzip writing and
    image values/geometry. This costs compressed random-read speed on Windows.
    """
    if (system or platform.system()) != "Windows":
        return False
    import nibabel.openers as openers

    modules = [openers]
    try:
        from nibabel import _compression
    except ImportError:
        pass
    else:
        modules.append(_compression)
    configured = False
    for module in modules:
        if hasattr(module, "HAVE_INDEXED_GZIP"):
            module.HAVE_INDEXED_GZIP = False
            configured = True
    if not configured:
        raise RuntimeError("Unsupported NiBabel gzip dispatch; Windows Unicode safety unverified")
    return True

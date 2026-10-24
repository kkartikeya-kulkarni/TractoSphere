"""Versioned local shell preferences; never write preferences beside images."""

import platform
from pathlib import Path

from PySide6.QtCore import QSettings

from dmri_studio.core.dependencies import INSTALL_LINKS, TOOLS, _candidate


class Settings:
    """A small injectable QSettings wrapper (tests use a temporary INI file)."""

    def __init__(self, store: QSettings | None = None) -> None:
        """Use the platform's user settings location by default."""
        self.store = store if store is not None else QSettings("dMRI Studio", "dMRI Studio")
        self.store.setValue("schema_version", 1)

    def get(self, key: str, default: object = None) -> object:
        """Return a preference with the type of the supplied default."""
        if default is None:
            return self.store.value(key)
        return self.store.value(key, default, type=type(default))

    def set(self, key: str, value: object) -> None:
        """Store a shell preference, never a dataset or image payload."""
        self.store.setValue(key, value)

    def folder(self, role: str) -> str:
        """Start native dialogs in the last existing folder, otherwise Home."""
        path = Path(str(self.get(f"folders/{role}", str(Path.home()))))
        return str(path if path.is_dir() else Path.home())


class ScientificSoftwareSettings:
    """The application-owned source for external software defaults.

    Existing ``dependencies/<family>`` keys are the on-disk format. A path is
    only a default: each scientific compiler still validates its exact tools.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def locations(self) -> dict[str, str]:
        return {
            family: str(self.settings.get(f"dependencies/{family}", "") or "")
            for family in INSTALL_LINKS
        }

    def location(self, family: str) -> Path | None:
        if family not in INSTALL_LINKS:
            raise KeyError(family)
        value = self.locations()[family].strip()
        return Path(value).expanduser() if value else None

    def state(self, family: str) -> str:
        """Cheap display state; no version query or scientific approval."""
        path = self.location(family)
        if path is None:
            return "not_configured"
        if platform.system() not in {"Darwin", "Linux"}:
            return "unsupported"
        if not path.is_dir():
            return "missing"
        folder = path / "bin" if (path / "bin").is_dir() else path
        if not _candidate(folder, TOOLS[family][0], None):
            return "incomplete"
        return "configured"

    def replace(self, family: str, path: str | None) -> None:
        """Commit a verified path or explicit removal; roll back failed writes."""
        if family not in INSTALL_LINKS:
            raise KeyError(family)
        key = f"dependencies/{family}"
        old = self.settings.get(key, "")
        if path:
            self.settings.set(key, str(path))
        else:
            self.settings.store.remove(key)
        self.settings.store.sync()
        if self.settings.store.status() != QSettings.Status.NoError:
            if old:
                self.settings.set(key, old)
            else:
                self.settings.store.remove(key)
            self.settings.store.sync()
            raise OSError(
                "Could not save application settings; previous configuration retained. Check preferences permissions."
            )

"""Versioned local shell preferences; never write preferences beside images."""

from pathlib import Path

from PySide6.QtCore import QSettings


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

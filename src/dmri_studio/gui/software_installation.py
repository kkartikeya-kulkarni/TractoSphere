"""Guided external installation inside the existing dependency manager.

The browser owns downloads/installers. No execution, password prompt or remote
script occurs here. Local checksum and rescan tasks reuse DependencyController.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.software_setup import destination_check, family_state, installation_guide

from .components import button, label

STATE_LABELS = {
    "not_checked": "Not checked · Scan Again",
    "missing": "Missing · installation guidance available",
    "ready": "Installed · version/path checks ready",
    "incompatible": "Installed but incompatible · additional validation required",
    "incomplete": "Incomplete installation · some operations may remain unavailable",
    "unsupported_os": "Unsupported scientific execution on this operating system",
    "interrupted": "Verification interrupted · no configuration saved",
}


class InstallationGuideDialog(QDialog):
    """One package detail sheet; uses the parent's scanner and settings transaction."""

    def __init__(self, manager, family):
        super().__init__(manager)
        self.manager, self.controller, self.family = manager, manager.controller, family
        self.guide = installation_guide(family)
        self.pending = self.archive_request = None
        self.external_started = False
        self.setWindowTitle(f"TractoSphere · {family} installation guidance")
        self.resize(860, 760)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(label(f"Set up {family}", "title"))
        host = self.guide["host"]
        os_label = "macOS" if host["system"] == "Darwin" else host["system"]
        body.addWidget(
            label(
                f"{os_label} · {host['architecture']} · {self.guide['platform_note']}",
                "caption",
            )
        )
        self.state = label("", "section")
        body.addWidget(self.state)
        body.addWidget(label("Required: " + self.guide["required_version"], "control"))
        body.addWidget(label(self.guide["method"], "caption"))
        info = label("", "caption")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        import html

        info.setText(
            "<ol>"
            + "".join("<li>" + html.escape(step) + "</li>" for step in self.guide["steps"])
            + "</ol>"
        )
        info.setAccessibleName(f"{family} installation steps")
        body.addWidget(info)
        links = QHBoxLayout()
        links.addWidget(
            button(
                "Official Installation Guide", lambda: self.open_browser(self.guide["guide_url"])
            )
        )
        links.addWidget(
            button(
                "Pinned Release / Installer Information",
                lambda: self.open_browser(self.guide["release_url"]),
            )
        )
        body.addLayout(links)
        body.addWidget(label("Installation destination / extracted folder", "control"))
        self.destination = QLineEdit(
            manager.fields[family].text() or self.detected_folder() or self.guide["destination"]
        )
        self.destination.setAccessibleName(f"{family} installation destination")
        body.addWidget(self.destination)
        self.space = label("", "caption")
        body.addWidget(self.space)
        self.destination.editingFinished.connect(self.check_destination)
        known = self.guide["download_bytes"]
        size = (
            f"{known:,} bytes ({known / 1024**2:.1f} MiB)"
            if known is not None
            else "Unknown; no fabricated download estimate"
        )
        body.addWidget(
            label(
                "Download: "
                + size
                + "\n"
                + self.guide["license"]
                + "\n"
                + self.guide["permissions"],
                "caption",
            )
        )
        body.addWidget(
            label(
                "Automatic installation unavailable: " + self.guide["automatic_reason"], "caption"
            )
        )
        self.consent = QCheckBox(
            "Open the pinned download in my browser; I will preserve existing installations"
        )
        body.addWidget(self.consent)
        self.consent.setVisible(bool(self.guide["download_url"]))
        self.download = button("Open Official Download", self.open_download)
        self.download.setEnabled(False)
        self.download.setToolTip(
            "Browser handoff only. TractoSphere does not run or install the downloaded file."
        )
        self.consent.toggled.connect(self.update_actions)
        body.addWidget(self.download)
        self.download.setVisible(bool(self.guide["download_url"]))
        self.archive = button("Verify Downloaded Archive", self.verify_download)
        self.archive.setToolTip(
            "Checks the selected local file against the official release SHA-256; does not extract or execute it."
        )
        body.addWidget(self.archive)
        self.archive.setVisible(bool(self.guide["sha256"]))
        self.checksum = label(
            "SHA-256: "
            + (
                self.guide["sha256"]
                or "No official release asset digest provided; no authenticity claim."
            ),
            "caption",
        )
        self.checksum.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.checksum)
        actions = QHBoxLayout()
        self.locate = button("Locate Existing Installation", self.locate_folder)
        self.scan = button("Scan Again", self.scan_again)
        self.test = button("Test Installation", self.test_installation)
        actions.addWidget(self.locate)
        actions.addWidget(self.scan)
        actions.addWidget(self.test)
        body.addLayout(actions)
        self.configure = QCheckBox("After a successful test, save verified paths in TractoSphere")
        self.configure.setChecked(True)
        body.addWidget(self.configure)
        self.status = label(
            "Installation is external. Nothing is downloaded, executed or changed by opening this guide.",
            "caption",
        )
        body.addWidget(self.status)
        self.failure = button("Installation Failed / Interrupted", self.report_interrupted)
        body.addWidget(self.failure)
        body.addWidget(
            label(
                "Version/path readiness is not scientific preprocessing validity. Viewer, tracts, atlases and ROIs remain available.",
                "caption",
            )
        )
        scroll.setWidget(content)
        root.addWidget(scroll)
        footer = QHBoxLayout()
        footer.addWidget(button("Cancel Verification", self.cancel_check))
        footer.addWidget(button("Done", self.accept))
        root.addLayout(footer)
        self.controller.updated.connect(self.refresh)
        self.controller.busy_changed.connect(self.busy_changed)
        self.controller.archive_checked.connect(self.archive_result)
        self.finished.connect(self.closed)
        self.check_destination()
        self.refresh()
        self.update_actions()

    def detected_folder(self):
        """Offer the actual inventoried folder instead of requiring PATH knowledge."""
        paths = {
            str(Path(r["path"]).parent)
            for r in (self.controller.report or {}).get("tools", [])
            if r["family"] == self.family and r["path"]
        }
        return next(iter(paths)) if len(paths) == 1 else ""

    def refresh(self, *_):
        """Read the authoritative manager report; no second detector or copied metrics."""
        value = family_state(self.controller.report, self.family, self.guide["host"])
        self.state.setText(STATE_LABELS[value["state"]])
        detail = []
        if value["missing"]:
            detail.append("Missing: " + ", ".join(value["missing"]))
        detail.extend(value["problems"])
        self.state.setToolTip("\n".join(detail))
        self.update_actions()

    def check_destination(self):
        """Inspect space/permission read-only; never create the suggested prefix."""
        self.storage = destination_check(self.destination.text().strip(), self.guide)
        free = self.storage["free_bytes"]
        text = (
            f"Available storage: {free / 1024**3:.2f} GiB. "
            if free is not None
            else "Available storage unknown. "
        )
        self.space.setText(text + self.storage["detail"])
        self.update_actions()

    def update_actions(self, *_):
        """No download without explicit consent; failed resources are explained visibly."""
        if not hasattr(self, "download"):
            return
        busy = self.controller.thread is not None
        storage = getattr(self, "storage", {})
        self.download.setEnabled(
            bool(self.guide["download_url"])
            and self.consent.isChecked()
            and not busy
            and storage.get("writable", False)
            and storage.get("sufficient") is not False
        )
        self.archive.setEnabled(bool(self.guide["sha256"]) and not busy)
        for name in ("scan", "test", "locate"):
            if hasattr(self, name):
                getattr(self, name).setEnabled(not busy)
        self.destination.setEnabled(not busy)
        if hasattr(self, "test"):
            self.test.setEnabled(not busy and self.guide["execution_supported"])

    def open_browser(self, url):
        """Explicit browser request only; success is not proof of network/download success."""
        if QDesktopServices.openUrl(QUrl(url)):
            self.external_started = True
            self.status.setText(
                "Official page opened externally. Download/installation status is not monitored; return to test the completed folder. If offline, these instructions remain readable."
            )
        else:
            self.status.setText(
                "Could not open the official page. Check your browser/network or copy the official URL from the setup report. No settings changed. Offline instructions remain available."
            )

    def open_download(self):
        """Recheck consent/resources rather than trusting a previously enabled button."""
        self.check_destination()
        if self.download.isEnabled():
            self.open_browser(self.guide["download_url"])

    def locate_folder(self):
        """Select a completed installation, not a guessed executable or system PATH edit."""
        path = QFileDialog.getExistingDirectory(
            self, "Select extracted " + self.family + " root/bin folder"
        )
        if path:
            self.destination.setText(path)
            self.check_destination()

    def scan_again(self):
        """Reuse the existing bounded autodiscovery; save nothing without Test consent."""
        self.pending = None
        target = self.destination.text().strip()
        if target and Path(target).expanduser().is_dir():
            self.manager.fields[self.family].setText(target)
        self.manager.scan()

    def test_installation(self):
        """Scan the user's selected folder and optionally configure only this family."""
        if self.controller.thread is not None or not self.guide["execution_supported"]:
            return
        candidate = self.destination.text().strip()
        if not candidate or not Path(candidate).expanduser().is_dir():
            self.status.setText(
                "Select the completed extracted installation folder. Incorrect/missing directory; no settings changed."
            )
            return
        self.manager.fields[self.family].setText(candidate)
        directories = {f: w.text().strip() for f, w in self.manager.fields.items()}
        request = self.controller.scan(directories, self.manager.common.isChecked())
        self.pending = request
        self.status.setText(
            "Testing actual executables/version evidence; no MRI processing. Configuration waits for verified results."
        )

    def busy_changed(self, busy):
        """Apply only the matching completed test, not a stale scan or another dialog's result."""
        self.update_actions()
        if busy or self.pending is None:
            return
        request = self.pending
        self.pending = None
        if self.controller.cancel.is_set() or not self.isVisible():
            self.status.setText(
                "Verification interrupted or dialog closed; no configuration saved."
            )
            return
        if self.controller.last_report_request != request or not self.manager.choices_current():
            self.status.setText(
                "Test choices changed or result was superseded. Test again; no configuration saved."
            )
            return
        if (
            self.controller.error
            or family_state(self.controller.report, self.family, self.guide["host"])["state"]
            != "ready"
        ):
            self.status.setText(
                "Installation test failed/incomplete/incompatible: "
                + (self.controller.error or self.state.text())
                + ". Check tool details and the selected extracted folder; no settings saved."
            )
            return
        if not self.configure.isChecked():
            self.status.setText(
                "Version/path checks passed; automatic configuration was not selected. Scientific review remains separate."
            )
            return
        if not self.manager.can_apply():
            self.status.setText(
                "Finish the active preprocessing run/preview before configuring tools. No settings changed."
            )
            return
        try:
            path = self.controller.apply_verified_family(self.family)
            self.destination.setText(path)
            self.status.setText(
                "Installation verified and configured in TractoSphere. Future processing revalidates binaries, versions and provenance. Scientific acceptance remains pending."
            )
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def verify_download(self):
        """Read-only local SHA check, owned by the shared setup worker."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Verify exact pinned archive", "", "Archive (*.zip)"
        )
        if path:
            self.archive_request = self.controller.check_archive(Path(path), self.guide)
            self.status.setText(
                "Reading downloaded archive SHA-256. No extraction or installation."
            )

    def archive_result(self, report, error):
        """A checksum match verifies archive integrity, not its installation or scientific outputs."""
        if self.archive_request != self.controller.sequence:
            return
        self.archive_request = None
        self.status.setText(
            "Archive verification failed/interrupted: " + error + " No settings changed."
            if error
            else "Official release SHA-256 and size matched. Extract externally, then select the full install folder and Test Installation."
        )

    def report_interrupted(self):
        """User-reported external failure is never represented as a monitored installer result."""
        self.pending = None
        self.status.setText(
            "External installation reported failed/interrupted by user. Previous settings retained. Do not use partial files; follow upstream recovery or choose a new destination and test the completed installation."
        )

    def cancel_check(self):
        """Cancel this dialog's setup task only; never touch scientific processes."""
        if (
            self.pending == self.controller.sequence
            or self.archive_request == self.controller.sequence
        ):
            self.controller.cancel.set()
            self.pending = self.archive_request = None
            self.status.setText(
                "Verification cancellation requested; no configuration will be saved."
            )

    def closed(self, *_):
        """Closing abandons configuration intent; an inventory may finish for the manager."""
        if self.archive_request == self.controller.sequence:
            self.controller.cancel.set()
        self.pending = self.archive_request = None

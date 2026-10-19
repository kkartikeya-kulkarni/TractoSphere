"""Boundary contract for accepted DTI, CSD and Stage 4 tracking adapters.

An accepted adapter must compile a reviewed Task into the existing ExecutionPlan
and use the existing engine's run_command/MRtrixStep validation/run/QC contract.
It may not introduce a GUI-owned process, shell command, or alternative runner.
The protocol is a design/interface contract, not a claim an adapter exists.
"""

from __future__ import annotations

from typing import Protocol

from ..execution import ExecutionPlan
from .contracts import Artifact
from .planning import Task


class ScientificAdapter(Protocol):
    """Per-algorithm implementations must be independently version/QC accepted."""

    def validate(self, task: Task, inputs: dict[str, Artifact]) -> tuple[dict, ...]:
        """Validate acquisition, geometry, conventions, versions and prerequisites."""
        ...

    def compile(
        self, task: Task, inputs: dict[str, Artifact], output_directory: str
    ) -> ExecutionPlan:
        """Return exact argv, immutable outputs, source/binary hashes and policy."""
        ...

    def run(self, plan: ExecutionPlan, *, approval: str, cancelled, progress) -> dict:
        """Delegate to the shared execution dispatcher and existing step runner.

        Implementations retain exact-digest approval, source/binary revalidation,
        immutable directories, resource logs and cancellation. This protocol
        does not implement or authorize an alternative executor.
        """
        ...

    def qc(self, manifest: dict) -> dict:
        """Verify outputs, mapping and diagnostic validity; never infer acceptance."""
        ...


# Each accepted ID has an actual compiler, shared-engine dispatcher
# and known-answer/output/QC tests; the protocol never activates planned entries.
ACCEPTED_ADAPTERS = (
    "registration.reference",
    "registration.qc",
    "registration.flirt",
    "registration.epi_reg",
    "registration.fnirt",
    "registration.invert",
    "registration.compose",
    "registration.convert",
    "registration.apply.scalar",
    "registration.apply.labels",
    "registration.apply.roi",
    "registration.apply.probability",

    "tcksift2",
    "dwi2tensor",
    "tensor2metric",
    "dwi2response.dhollander",
    "dwi2response.tournier",
    "dwi2response.tax",
    "dwi2fod.csd",
    "dwi2fod.msmt_csd",
    "mtnormalise",
    "tckgen.ifod2",
    "tckgen.sdstream",
    "tckgen.ifod1",
    "tckgen.tensor_det",
    "tckgen.tensor_prob",
    "tckedit",
    "tckmap",
    "tck2connectome",
    "connectome2tck",
    "5ttgen.fsl",
    "5ttcheck",
    "5tt2vis",
    "5tt2gmwmi",
    "tckgen.act_ifod2",
    "tckgen.act_ifod1",
    "tckgen.act_sdstream",
)

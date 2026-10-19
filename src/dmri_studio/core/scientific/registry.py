"""Declarative algorithm contracts with explicitly accepted execution adapters.

DTI, CSD/response/normalization and Stage 4 tracking have accepted 3.0.5 adapters.
ACT, five-tissue preparation and connectomics have accepted adapters; SIFT2
is implemented. Version acceptance is algorithm specific.
Parameters are deliberately unset, never copied from an unrelated protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..tool_versions import MRTRIX_TESTED_RELEASES
from ..validation import freeze_json, json_compatible

BASE = "https://mrtrix.readthedocs.io/en/latest/reference/commands/"


@dataclass(frozen=True)
class Algorithm:
    """An independently schedulable operation's public scientific contract."""

    id: str
    name: str
    stage: str
    command: str
    required_inputs: Any
    optional_inputs: Any
    acquisition_types: tuple[str, ...]
    parameters: Any
    outputs: Any
    qc: tuple[str, ...]
    backend: str = "MRtrix3 local CLI"
    implementation: str = "planned"
    validated_versions: tuple[str, ...] = ()
    adapter: str | None = None

    def __post_init__(self) -> None:
        """Freeze the contract and reject ambiguous or unsupported input meaning."""
        for field in ("required_inputs", "optional_inputs", "parameters", "outputs"):
            object.__setattr__(self, field, freeze_json(getattr(self, field)))

    def to_dict(self) -> dict:
        """Return detached JSON-compatible contract evidence for review or persistence."""
        return json_compatible(
            dict(
                **self.__dict__,
                dependencies=[self.command]
                if self.stage == "registration"
                else ["tcksift2", "tckinfo", "5ttcheck"]
                if self.id == "tcksift2"
                else ["mrconvert", "tck2connectome", "tcksample"]
                if self.id == "tck2connectome"
                else sorted({"mrconvert", "tckinfo", "tckstats", self.command})
                if self.stage == "tractography" and self.implementation == "implemented"
                else ["mrconvert", self.command]
                if self.implementation == "implemented"
                else [self.command],
                dependency_scope="Adapter probes exact commands; CSD response scripts additionally bind their Python runtime, helper executables and library source hashes"
                if self.implementation == "implemented"
                else "Entry-point probes only; transitive script/helper dependencies must be pinned and tested before adapter acceptance",
                version_policy=dict(
                    validated_adapter_versions=self.validated_versions,
                    candidate_preprocessing_releases=sorted(MRTRIX_TESTED_RELEASES),
                    policy="Algorithm-specific acceptance required; preprocessing validation does not activate this adapter",
                ),
                default_policy="Scientific parameters unset until explicit selection; no implicit upstream defaults",
                source="https://fsl.fmrib.ox.ac.uk/fsl/docs/registration/index.html"
                if self.stage == "registration"
                else BASE + self.command + ".html",
                output_verification="Every expected output must be verified before downstream consumption; exit status alone is insufficient",
            )
        )


def parameter(
    kind: str,
    *,
    choices=(),
    minimum=None,
    maximum=None,
    required=True,
    policy="Explicit user selection",
) -> dict:
    """Portable typed parameter schema, reusable by CLI/forms and future adapters."""
    return dict(
        type=kind,
        choices=list(choices),
        minimum=minimum,
        maximum=maximum,
        required=required,
        default=None,
        policy=policy,
    )


def _registry() -> dict[str, Algorithm]:
    """Declare independently tested variants; keep future adapters visibly planned."""
    common = (
        "finite values and geometry",
        "source hashes unchanged",
        "scientific review remains required",
    )
    shells = parameter(
        "number_list", minimum=0, policy="Explicit b-value groups; no automatic shell removal"
    )
    lmax = parameter(
        "int",
        minimum=2,
        maximum=30,
        policy="Even SH order; directional sufficiency must be verified",
    )
    rows = [
        Algorithm(
            "dwi2tensor",
            "Diffusion tensor estimation",
            "models",
            "dwi2tensor",
            {"dwi": "dwi"},
            {"mask": "mask"},
            ("single_shell", "multi_shell"),
            {
                "shells": shells,
                "fit": parameter("choice", choices=("IWLS", "WLS", "OLS", "WLS_IWLS")),
                "iterations": parameter(
                    "int",
                    minimum=0,
                    maximum=10,
                    required=False,
                    policy="IWLS: explicit 1..10 reweightings after initial WLS. WLS: 0. OLS: absent (compiler emits -ols -iter 0). WLS_IWLS is a legacy Stage1 selection alias.",
                ),
            },
            {"tensor": "tensor"},
            common
            + (
                "tensor fit residuals",
                "design rank and selected volume mapping",
                "negative eigenvalue/coverage review",
            ),
        ),
        Algorithm(
            "tensor2metric",
            "Tensor metrics from existing tensor",
            "models",
            "tensor2metric",
            {"tensor": "tensor"},
            {"mask": "mask"},
            ("existing_tensor",),
            {
                "metrics": parameter(
                    "choice_list",
                    choices=("FA", "MD", "AD", "RD", "V1", "EIGENVALUES", "CL", "CP", "CS"),
                )
            },
            {"metrics": "tensor_metrics"},
            common + ("eigenvalue/vector orientation and metric invariants",),
        ),
        Algorithm(
            "dwi2response.tournier",
            "WM response · Tournier",
            "models",
            "dwi2response",
            {"dwi": "dwi", "mask": "mask"},
            {},
            ("single_shell",),
            {
                "shells": shells,
                "lmax": lmax,
                "max_iters": parameter("int", minimum=2, maximum=100, required=False),
                "number": parameter("int", minimum=1, maximum=100000, required=False),
            },
            {"wm_response": "response"},
            common + ("response curves, selected voxels and shell correspondence",),
        ),
        Algorithm(
            "dwi2response.dhollander",
            "WM/GM/CSF responses · Dhollander",
            "models",
            "dwi2response",
            {"dwi": "dwi", "mask": "mask"},
            {},
            ("single_shell", "multi_shell"),
            {
                "shells": shells,
                "lmax": lmax,
                "erode": parameter("int", minimum=0, maximum=10, required=False),
            },
            {"wm_response": "response", "gm_response": "response", "csf_response": "response"},
            common + ("tissue response and voxel selection review",),
        ),
        Algorithm(
            "dwi2response.tax",
            "WM response · Tax",
            "models",
            "dwi2response",
            {"dwi": "dwi", "mask": "mask"},
            {},
            ("single_shell",),
            {
                "shells": shells,
                "lmax": lmax,
                "max_iters": parameter("int", minimum=1, maximum=100, required=False),
                "peak_ratio": parameter("number", minimum=0.001, maximum=1, required=False),
                "convergence": parameter("number", minimum=0.001, maximum=100, required=False),
            },
            {"wm_response": "response"},
            common + ("recursive response calibration, selected voxels and convergence review",),
        ),
        Algorithm(
            "dwi2fod.csd",
            "Single-shell WM CSD",
            "models",
            "dwi2fod",
            {"dwi": "dwi", "wm_response": "response", "mask": "mask"},
            {},
            ("single_shell",),
            {
                "shells": shells,
                "lmax": lmax,
                "niter": parameter("int", minimum=1, maximum=1000, required=False),
                "threshold": parameter("number", minimum=0, required=False),
                "neg_lambda": parameter("number", minimum=1e-12, required=False),
                "norm_lambda": parameter("number", minimum=1e-12, required=False),
            },
            {"wm_fod": "fod"},
            common + ("SH basis/frame/order", "FOD lobes, response and prediction residuals"),
        ),
        Algorithm(
            "dwi2fod.msmt_csd",
            "Three-tissue MSMT-CSD",
            "models",
            "dwi2fod",
            {
                "dwi": "dwi",
                "wm_response": "response",
                "gm_response": "response",
                "csf_response": "response",
                "mask": "mask",
            },
            {},
            ("multi_shell",),
            {
                "shells": shells,
                "lmax": lmax,
                "neg_lambda": parameter("number", minimum=1e-12, required=False),
                "norm_lambda": parameter("number", minimum=1e-12, required=False),
                "predict_signal": parameter(
                    "choice",
                    choices=("off", "on"),
                    required=False,
                    policy="Explicit optional full selected-signal prediction; storage estimate includes this output",
                ),
            },
            {"wm_fod": "fod", "gm": "tissue_scalar", "csf": "tissue_scalar"},
            common
            + (
                "three unique selected b-values including b0",
                "response shells/tissue correspondence",
                "tissue leakage and prediction residuals",
            ),
        ),
        Algorithm(
            "mtnormalise",
            "Three-tissue intensity normalization",
            "models",
            "mtnormalise",
            {"wm_fod": "fod", "gm": "tissue_scalar", "csf": "tissue_scalar", "mask": "mask"},
            {},
            ("existing_fod",),
            {
                "order": parameter("int", minimum=0, maximum=10),
                "balanced": parameter(
                    "choice",
                    choices=("off", "on"),
                    policy="Explicit choice; balancing changes quantitative interpretation",
                ),
                "iterations": parameter("int", minimum=1, maximum=100, required=False),
            },
            {"wm_fod": "fod", "gm": "tissue_scalar", "csf": "tissue_scalar"},
            common
            + (
                "normalization field/factors and tissue intensity review",
                "prevent repeated normalization",
            ),
        ),
        Algorithm(
            "tckgen.act_ifod2",
            "Anatomically constrained iFOD2",
            "tractography",
            "tckgen",
            {"fod": "fod", "seed_mask": "mask", "five_tt": "5tt"},
            {},
            ("existing_fod",),
            {
                "select": parameter("int", minimum=1, maximum=100000000),
                "step_mm": parameter("number", minimum=0.001),
                "cutoff": parameter("number", minimum=0),
                "angle_deg": parameter("number", minimum=0, maximum=90),
            },
            {"tracts": "tractogram"},
            common + ("verified 5TT/anatomical registration and tissue termination",),
        ),
    ]
    from .sift2_registry import sift2_algorithm

    rows.append(sift2_algorithm(Algorithm, parameter))
    from .tracking_registry import tracking_algorithms

    rows.extend(tracking_algorithms(Algorithm, parameter))
    from .connectome_registry import connectome_algorithms

    rows.extend(connectome_algorithms(Algorithm, parameter))
    from .anatomy_registry import anatomy_algorithms

    rows.extend(anatomy_algorithms(Algorithm, parameter))
    from .registration_registry import registration_algorithms

    rows.extend(registration_algorithms(Algorithm, parameter))
    from dataclasses import replace

    return {
        a.id: replace(
            a,
            implementation="implemented",
            validated_versions=("3.0.5",),
            adapter="dmri_studio.core.scientific.dti.DTIAdapter"
            if a.id in {"dwi2tensor", "tensor2metric"}
            else "dmri_studio.core.scientific.csd.CSDAdapter",
        )
        if a.id
        in {
            "dwi2tensor",
            "tensor2metric",
            "dwi2response.tournier",
            "dwi2response.tax",
            "dwi2response.dhollander",
            "dwi2fod.csd",
            "dwi2fod.msmt_csd",
            "mtnormalise",
        }
        else a
        for a in rows
    }


ALGORITHMS = MappingProxyType(_registry())

"""New batch defaults are explicit inputs to the existing scientific compilers."""

from dmri_studio.core.batch import planning
from dmri_studio.core.dependencies import fsl_root


def test_registration_and_anatomy_receive_fsl_without_environment(monkeypatch, tmp_path):
    calls = []

    def fake_compiler(name):
        return lambda *args, **kwargs: calls.append((name, kwargs))

    monkeypatch.setattr(planning, "compiler", fake_compiler)
    fsl = tmp_path / "fsl" / "bin"
    for adapter in ("registration", "anatomy", "dti"):
        planning.compile_node(
            None,
            {"adapter": adapter},
            (),
            (),
            tmp_path / "output",
            tool_directory=tmp_path / "mrtrix" / "bin",
            fsl_directory=fsl,
            ants_directory=tmp_path / "ants" / "bin",
        )
    assert calls[0][1]["fsl_directory"] == fsl
    assert calls[1][1]["fsl_directory"] == fsl
    assert "fsl_directory" not in calls[2][1]
    assert all("ants_directory" not in kwargs for _, kwargs in calls)


def test_explicit_fsl_root_overrides_environment_and_empty_is_not_cwd(tmp_path):
    selected = tmp_path / "selected" / "bin"
    other = tmp_path / "other"
    assert fsl_root(selected, {"FSLDIR": str(other)}) == selected.parent
    assert fsl_root(selected.parent, {"FSLDIR": str(other)}) == selected.parent
    assert fsl_root(None, {"FSLDIR": str(other)}) == other
    assert fsl_root(None, {}) is None

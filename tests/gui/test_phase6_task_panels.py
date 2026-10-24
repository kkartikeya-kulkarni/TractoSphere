"""Guided scientific controls retain typed plans and conditional inputs."""


def test_remaining_task_panels_show_only_relevant_policy_controls(window):
    page = window.scientific
    window.focus_models()
    csd = page.csd
    assert "Three-tissue" in csd.method_info.text()
    csd.method.setCurrentIndex(csd.method.findData("dwi2fod.csd"))
    assert "Single-tissue" in csd.method_info.text()
    assert csd.plan is None and not csd.approval.isChecked()

    window.focus_tracking_plan()
    anatomy = page.anatomy
    anatomy.operation.setCurrentIndex(anatomy.operation.findData("5tt2vis"))
    assert "existing 5TT" in anatomy.task_help.text()
    assert not anatomy.form.isRowVisible(anatomy.extraction)
    assert "reviewed diffusion reference" in anatomy.input_help.text()
    anatomy.operation.setCurrentIndex(anatomy.operation.findData("5ttgen.fsl"))
    assert anatomy.form.isRowVisible(anatomy.extraction)

    sift2 = page.sift2
    sift2.policy.setCurrentIndex(sift2.policy.findData("mask"))
    assert sift2.form.isRowVisible(sift2.input_rows["proc_mask"])
    assert not sift2.form.isRowVisible(sift2.input_rows["five_tt"])
    assert "processing mask" in sift2.input_help.text()
    sift2.policy.setCurrentIndex(sift2.policy.findData("act"))
    assert sift2.form.isRowVisible(sift2.input_rows["five_tt"])
    assert not sift2.form.isRowVisible(sift2.input_rows["proc_mask"])
    assert sift2.plan is None and not sift2.approval.isChecked()

    window.focus_connectivity()
    connectome = page.connectome
    connectome.assignment.setCurrentIndex(connectome.assignment.findData("end_voxels"))
    assert not connectome.form.isRowVisible(connectome.radius)
    assert not connectome.form.isRowVisible(connectome.search)
    assert "registered atlas" in connectome.input_help.text()
    connectome.assignment.setCurrentIndex(connectome.assignment.findData("radial_search"))
    assert connectome.form.isRowVisible(connectome.radius)
    assert not connectome.form.isRowVisible(connectome.search)
    assert connectome.plan is None and not connectome.approve.isChecked()

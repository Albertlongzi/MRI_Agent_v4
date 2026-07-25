from __future__ import annotations

from packages.planner.compiler import build_intent_spec, compile_intent_spec
from packages.schemas.models import ActionGraph, CaseState
from packages.tools.compiler_metadata import get_tool_contract


def _case_state(domain: str) -> CaseState:
    return CaseState(case_id=f"{domain}_case", domain=domain, input_root=f"/tmp/{domain}_case")


def test_compiler_input_has_contract_dependency_and_expansion_sections() -> None:
    intent = build_intent_spec(
        user_message="Inspect this prostate lesion case and generate a report.",
        case_state=_case_state("prostate"),
        graph=None,
        domain="prostate",
        requested_capabilities=["lesion", "report"],
        available_capabilities=["full_pipeline", "register", "segment", "classify", "report", "lesion"],
        available_tools=[
            "identify_sequences",
            "register_to_reference",
            "segment_prostate",
            "detect_lesion_candidates",
            "extract_roi_features",
            "package_vlm_evidence",
            "generate_report",
        ],
    )

    result = compile_intent_spec(intent)

    assert "tool_contracts" in result.compiler_input
    assert "dependency_rules" in result.compiler_input
    assert "capability_expansion_rules" in result.compiler_input
    assert any(contract["tool_name"] == "detect_lesion_candidates" for contract in result.compiler_input["tool_contracts"])
    assert any(contract["tool_name"] == "extract_roi_features" for contract in result.compiler_input["tool_contracts"])
    assert "prostate-lesion-expansion" in result.applied_rules


def test_prostate_lesion_request_compiles_to_expanded_dependency_graph() -> None:
    intent = build_intent_spec(
        user_message="Inspect this prostate lesion case and report the findings.",
        case_state=_case_state("prostate"),
        graph=None,
        domain="prostate",
        requested_capabilities=["lesion", "report"],
        available_capabilities=["full_pipeline", "register", "segment", "classify", "report", "lesion"],
        available_tools=[
            "identify_sequences",
            "register_to_reference",
            "segment_prostate",
            "detect_lesion_candidates",
            "extract_roi_features",
            "package_vlm_evidence",
            "generate_report",
        ],
    )

    result = compile_intent_spec(intent)
    graph = result.graph
    tool_names = [node.tool_name for node in graph.nodes if node.tool_name]
    nodes_by_tool = {node.tool_name: node for node in graph.nodes if node.tool_name}

    assert tool_names[:2] == ["identify_sequences", "register_to_reference"]
    assert "segment_prostate" in tool_names
    assert "detect_lesion_candidates" in tool_names
    assert "extract_roi_features" in tool_names
    assert nodes_by_tool["detect_lesion_candidates"].depends_on == ["segment_prostate"]
    assert nodes_by_tool["extract_roi_features"].depends_on == ["detect_lesion_candidates"]
    assert nodes_by_tool["generate_report"].depends_on == ["package_vlm_evidence"]


# ---------------------------------------------------------------------------
# Cardiac: raw k-space entry point
#
# A cardiac case whose input is an HDF5 k-space file cannot run a single
# image-domain tool until it has been reconstructed, so the compiler has to put
# ``reconstruct_grappa`` at the head of the chain and hang everything else off
# it.  The image-domain (NIfTI) cardiac case must keep compiling byte-for-byte
# as it did before that rule existed.
# ---------------------------------------------------------------------------


def _cardiac_intent(case_dir, capabilities):
    return build_intent_spec(
        user_message="Work this cardiac case up and report the findings.",
        case_state=CaseState(case_id="cardiac_case", domain="cardiac", input_root=str(case_dir)),
        graph=None,
        domain="cardiac",
        requested_capabilities=capabilities,
        available_capabilities=["full_pipeline", "segment", "classify", "report", "reconstruct"],
        available_tools=[
            "reconstruct_grappa",
            "identify_sequences",
            "segment_cardiac_cine",
            "classify_cardiac_cine_disease",
            "package_vlm_evidence",
            "generate_report",
        ],
    )


def test_raw_kspace_cardiac_case_compiles_reconstruction_first(tmp_path) -> None:
    case_dir = tmp_path / "kspace_case"
    case_dir.mkdir()
    (case_dir / "cine_sax.h5").write_bytes(b"\x89HDF\r\n\x1a\n")

    result = compile_intent_spec(_cardiac_intent(case_dir, ["full_pipeline"]))
    tool_names = [node.tool_name for node in result.graph.nodes if node.tool_name]
    nodes_by_tool = {node.tool_name: node for node in result.graph.nodes if node.tool_name}

    assert tool_names[0] == "reconstruct_grappa"
    assert tool_names == [
        "reconstruct_grappa",
        "identify_sequences",
        "segment_cardiac_cine",
        "classify_cardiac_cine_disease",
        "package_vlm_evidence",
        "generate_report",
    ]
    # The reconstruction leads: nothing upstream of it, and every image-domain
    # node reaches it through the dependency graph.
    assert nodes_by_tool["reconstruct_grappa"].depends_on == []
    assert nodes_by_tool["reconstruct_grappa"].inputs == {"h5_path": "@case.input"}
    assert nodes_by_tool["identify_sequences"].depends_on == ["reconstruct_grappa"]
    assert nodes_by_tool["segment_cardiac_cine"].depends_on == ["identify_sequences", "reconstruct_grappa"]
    assert "cardiac-raw-kspace-entry" in result.applied_rules
    assert result.warnings == []

    edges = {(edge.from_node, edge.to_node) for edge in result.graph.edges}
    assert ("reconstruct_grappa", "identify_sequences") in edges
    assert ("reconstruct_grappa", "segment_cardiac_cine") in edges


def test_image_domain_cardiac_case_compiles_without_reconstruction(tmp_path) -> None:
    """The NIfTI cardiac path must be untouched by the k-space rule."""
    case_dir = tmp_path / "nifti_case"
    case_dir.mkdir()
    (case_dir / "patient061_cine_4d.nii.gz").write_bytes(b"\x1f\x8b nifti-ish")

    result = compile_intent_spec(_cardiac_intent(case_dir, ["full_pipeline"]))
    tool_names = [node.tool_name for node in result.graph.nodes if node.tool_name]
    nodes_by_tool = {node.tool_name: node for node in result.graph.nodes if node.tool_name}

    assert "reconstruct_grappa" not in tool_names
    assert tool_names == [
        "identify_sequences",
        "segment_cardiac_cine",
        "classify_cardiac_cine_disease",
        "package_vlm_evidence",
        "generate_report",
    ]
    assert nodes_by_tool["identify_sequences"].depends_on == []
    assert nodes_by_tool["segment_cardiac_cine"].depends_on == ["identify_sequences"]
    assert "cardiac-raw-kspace-entry" not in result.applied_rules


def test_missing_case_input_never_invents_a_reconstruction_node(tmp_path) -> None:
    """An unreadable input_root is not evidence of k-space; the rule stays off."""
    result = compile_intent_spec(_cardiac_intent(tmp_path / "does_not_exist", ["full_pipeline"]))
    tool_names = [node.tool_name for node in result.graph.nodes if node.tool_name]
    assert "reconstruct_grappa" not in tool_names
    assert "cardiac-raw-kspace-entry" not in result.applied_rules


def test_reconstruct_capability_is_ignored_without_a_kspace_input(tmp_path) -> None:
    """Asking to "reconstruct" an image-domain case must not add a dead node.

    The capability rule used to fire on the wording alone, so a request
    mentioning reconstruction on a NIfTI case compiled a reconstruct_grappa
    node that could only ever fail with "found no .h5/.hdf5 file". It is now
    additionally gated on the case input.
    """
    case_dir = tmp_path / "nifti_case"
    case_dir.mkdir()
    (case_dir / "patient061_cine_4d.nii.gz").write_bytes(b"\x1f\x8b nifti-ish")

    result = compile_intent_spec(_cardiac_intent(case_dir, ["full_pipeline", "reconstruct"]))
    tool_names = [node.tool_name for node in result.graph.nodes if node.tool_name]

    assert "reconstruct_grappa" not in tool_names
    assert "cardiac-kspace-reconstruction" not in result.applied_rules
    assert tool_names[0] == "identify_sequences"
    # the skip is reported rather than silent
    assert any("cardiac-kspace-reconstruction" in w for w in result.warnings)


def test_reconstruct_capability_is_honoured_when_the_input_is_kspace(tmp_path) -> None:
    """The same request on a real k-space case still leads with reconstruction."""
    case_dir = tmp_path / "kspace_case"
    case_dir.mkdir()
    (case_dir / "Center006_P038_cine_sax.h5").write_bytes(b"\x89HDF\r\n\x1a\n")

    result = compile_intent_spec(_cardiac_intent(case_dir, ["full_pipeline", "reconstruct"]))
    tool_names = [node.tool_name for node in result.graph.nodes if node.tool_name]

    assert tool_names[0] == "reconstruct_grappa"


def test_reconstruct_grappa_has_compiler_metadata() -> None:
    contract = get_tool_contract("reconstruct_grappa")
    assert contract["domains"] == ["cardiac"]
    assert contract["required_inputs"] == ["h5_path"]
    assert contract["produced_outputs"] == ["reconstructed_nifti"]
    assert contract["runtime_profile"] == "recon-cpu"

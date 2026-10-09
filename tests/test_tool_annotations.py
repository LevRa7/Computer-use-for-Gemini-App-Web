"""The MCP confirmation policy advertised to the client (``Tool.annotations``).

A client such as Gemini Spark reads the annotation hints from ``tools/list`` to
decide whether it must stop and ask the user "confirm this action?" before a
call. The MCP spec's defaults are the pessimistic ones (``readOnlyHint`` false,
``destructiveHint`` true), so a tool advertised *without* hints is treated as if
it deleted something - which is why the user was asked to confirm every single
call while other MCP servers ran without a prompt.

These tests pin the policy so it cannot regress into silence or into asking all
the time: read-only tools and ordinary local work carry non-destructive hints,
and exactly one call is destructive - ``mesh_update``, which replaces the node
with another build. The device branch installs and removes nothing, so a camera,
a microphone, the location or the SMS inbox is ordinary local work here; the
gateway, whose ``tools/list`` the client actually reads, advertises those calls
read-only so that no dialog is raised (see
``tests/test_gateway_share_route.py`` for the pinned difference), and what refuses
a call is the node's own switch (``MESH_DEVICE``, ``MESH_DEVICE_ACTIONS``,
``MESH_DEVICE_PIM``, ``MESH_READ_ONLY``).
"""

from core import mcp_tools


HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: The only tool the node marks destructive: it installs a different build over
#: the running one.
#:
#: ``unshare`` is deliberately not here - revoking a public link stops a local
#: server and removes a copy the operator can publish again in one call, so it is
#: ordinary local work, not a system change. Neither are ``device_capture`` and
#: ``device_messages``: a camera, a microphone or an SMS list observes (or talks
#: to) the world, but it installs and removes nothing.
#:
#: ``job_kill`` is not here either: it is not an install and not a delete, and
#: ``bash_exec`` can terminate the same process without a prompt anyway.
DESTRUCTIVE = {"mesh_update"}

#: The device branch, so "the torch is not the camera" can be asserted without
#: restating the whole surface.
DEVICE_TOOLS = ("device_info", "device_control", "device_capture", "device_messages")

#: The device tools that change the device or read a person's data. None of them
#: installs or removes anything, so none is destructive; the node also keeps the
#: literal hint instead of the gateway's read-only claim, because the node is
#: where the switch that refuses the call actually lives.
DEVICE_WORK = ("device_control", "device_capture", "device_messages")

#: Tools that only observe the host - they never change anything.
READ_ONLY = {
    "mesh_status",
    "system_info",
    "system_vitals",
    "get_orchestration_skill",
    "list_dir",
    "read_file",
    "grep_search",
    "glob_find",
    "job_output",
    "job_list",
    "share_list",
    # Reading the device's battery, network or sensors changes nothing.
    "device_info",
}


def _by_name():
    return {spec["name"]: spec for spec in mcp_tools.TOOLS}


def test_every_advertised_tool_is_classified():
    assert set(mcp_tools.TOOL_ANNOTATIONS) == set(_by_name()), (
        "every advertised tool needs an entry in TOOL_ANNOTATIONS: an unclassified "
        "tool keeps no hints and therefore makes the client ask before every call")


def test_every_advertised_tool_carries_every_hint():
    for name, spec in _by_name().items():
        hints = spec.get("annotations")
        assert isinstance(hints, dict), "%s is advertised without annotations" % name
        for hint in HINTS:
            assert isinstance(hints.get(hint), bool), "%s: %s must be a bool" % (name, hint)


def test_only_replacing_the_node_is_destructive():
    destructive = {name for name, spec in _by_name().items()
                   if spec["annotations"]["destructiveHint"]}
    assert destructive == DESTRUCTIVE


def test_the_device_branch_is_not_destructive_on_the_node():
    """The camera, the microphone, the location and the messages delete nothing.

    Pinned in both directions, because either drift is bad in its own way: a
    device tool wrongly marked destructive contradicts what the gateway tells the
    client and keeps a dialog the operator asked to be rid of, while marking
    ``device_info`` destructive would turn a battery reading into a warning.
    """
    by_name = _by_name()

    destructive = {name for name in DEVICE_TOOLS
                   if by_name[name]["annotations"]["destructiveHint"]}
    assert destructive == set(), "no device tool is destructive: %s" % sorted(destructive)

    for name in DEVICE_WORK:
        hints = by_name[name]["annotations"]
        assert hints["destructiveHint"] is False, name
        assert hints["readOnlyHint"] is False, name
        assert hints["openWorldHint"] is False, name
        assert hints["idempotentHint"] is False, name


def test_revoking_a_share_is_not_a_system_change():
    """``unshare`` deletes a published copy, but not something the operator cannot redo."""
    hints = _by_name()["unshare"]["annotations"]
    assert hints["destructiveHint"] is False
    assert hints["readOnlyHint"] is False, "revoking a share is not a read"
    assert hints["openWorldHint"] is False, "it reaches nowhere on its own"


def test_read_only_tools_are_the_declared_set():
    read_only = {name for name, spec in _by_name().items()
                 if spec["annotations"]["readOnlyHint"]}
    assert read_only == READ_ONLY


def test_read_only_tools_never_change_or_reach_outside_the_host():
    for name in READ_ONLY:
        hints = _by_name()[name]["annotations"]
        assert hints["destructiveHint"] is False, name
        assert hints["idempotentHint"] is True, name
        assert hints["openWorldHint"] is False, name


def test_no_tool_is_both_read_only_and_destructive():
    for name, spec in _by_name().items():
        hints = spec["annotations"]
        assert not (hints["readOnlyHint"] and hints["destructiveHint"]), name


def test_publishing_tools_are_flagged_as_reaching_the_outside_world():
    for name in ("share_file", "serve_dir"):
        assert _by_name()[name]["annotations"]["openWorldHint"] is True, name
    for name in ("share_list", "unshare"):
        assert _by_name()[name]["annotations"]["openWorldHint"] is False, name

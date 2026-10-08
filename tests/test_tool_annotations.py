"""The MCP confirmation policy advertised to the client (``Tool.annotations``).

A client such as Gemini Spark reads the annotation hints from ``tools/list`` to
decide whether it must stop and ask the user "confirm this action?" before a
call. The MCP spec's defaults are the pessimistic ones (``readOnlyHint`` false,
``destructiveHint`` true), so a tool advertised *without* hints is treated as if
it deleted something - which is why the user was asked to confirm every single
call while other MCP servers ran without a prompt.

These tests pin the policy so it cannot regress into silence or into asking all
the time: read-only tools and ordinary local work carry non-destructive hints,
and only the calls that install something (``mesh_update``) or delete or revoke
it (``unshare``) are marked destructive.
"""

from core import mcp_tools


HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: The only tools allowed to interrupt the user with a confirmation prompt: the
#: ones that install something (``mesh_update``) or delete/revoke it (``unshare``).
#: ``job_kill`` is deliberately not here: it is not an install and not a delete,
#: and ``bash_exec`` can terminate the same process without a prompt anyway.
ASK_FIRST = {"mesh_update", "unshare"}

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


def test_only_installing_or_deleting_asks_for_confirmation():
    destructive = {name for name, spec in _by_name().items()
                   if spec["annotations"]["destructiveHint"]}
    assert destructive == ASK_FIRST


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

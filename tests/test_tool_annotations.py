"""The MCP confirmation policy advertised to the client (``Tool.annotations``).

A client such as Gemini Spark reads the annotation hints from ``tools/list`` to
decide whether it must stop and ask the user "confirm this action?" before a
call. The MCP spec's defaults are the pessimistic ones (``readOnlyHint`` false,
``destructiveHint`` true), so a tool advertised *without* hints is treated as if
it deleted something - which is why the user was asked to confirm every single
call while other MCP servers ran without a prompt.

These tests pin the policy so it cannot regress into silence or into asking all
the time: read-only tools and ordinary local work carry non-destructive hints,
and only two groups of calls ask first - the ones that install something
(``mesh_update``) or delete or revoke it (``unshare``), and the ones that reach
into the physical world or a person's private data (``device_capture``: camera,
microphone, location, fingerprint, USB, infrared; ``device_messages``: SMS, call
log, contacts, placing a call).
"""

from core import mcp_tools


HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: The only tools allowed to interrupt the user with a confirmation prompt, from
#: two groups:
#:
#: * install / delete - ``mesh_update`` installs something, ``unshare`` deletes or
#:   revokes a public link;
#: * physical world / privacy - ``device_capture`` and ``device_messages``
#:   observe the room the node sits in or the private messages of the person
#:   holding the phone. They delete nothing, so ``destructiveHint`` is not
#:   literally true; it is, however, the only hint clients such as Gemini Spark
#:   reliably turn into a "confirm this action?" prompt, and a camera or an SMS
#:   must not fire silently.
#:
#: ``job_kill`` is deliberately not here: it is not an install and not a delete,
#: and ``bash_exec`` can terminate the same process without a prompt anyway.
ASK_FIRST = {"mesh_update", "unshare", "device_capture", "device_messages"}

#: The sensitive profile, pinned by name. Every entry here is also in ASK_FIRST,
#: and the hint values are identical to the install/delete profile, so only the
#: names distinguish the two groups - which is exactly why they are spelled out.
#: A camera call must never be quietly reclassified as ordinary local work.
SENSITIVE = {"device_capture", "device_messages"}

#: The device branch, so "the torch is not the camera" can be asserted without
#: restating the whole surface.
DEVICE_TOOLS = ("device_info", "device_control", "device_capture", "device_messages")

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


def test_only_installing_deleting_or_sensitive_calls_ask_for_confirmation():
    destructive = {name for name, spec in _by_name().items()
                   if spec["annotations"]["destructiveHint"]}
    assert destructive == ASK_FIRST


def test_the_sensitive_group_is_exactly_capture_and_messages():
    """The camera/microphone/location and SMS/contacts tools ask; nothing else does.

    Pinned in both directions, because either drift is bad in its own way: mark a
    capture tool non-destructive and it can photograph the room without a prompt,
    while marking ``device_control`` destructive turns a torch or a vibrate into a
    confirmation dialog and trains the user to click through the prompts that
    matter.
    """
    by_name = _by_name()

    destructive_devices = {name for name in DEVICE_TOOLS
                           if by_name[name]["annotations"]["destructiveHint"]}
    assert destructive_devices == SENSITIVE

    for name in SENSITIVE:
        hints = by_name[name]["annotations"]
        assert hints["destructiveHint"] is True, name
        assert hints["readOnlyHint"] is False, name
        assert hints["openWorldHint"] is False, name
        assert hints["idempotentHint"] is False, name

    control = by_name["device_control"]["annotations"]
    assert control["destructiveHint"] is False, "a torch must not prompt"
    assert control["readOnlyHint"] is False, "device_control does change the device"
    assert control["openWorldHint"] is False


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

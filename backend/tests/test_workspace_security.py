import pytest

import main
from agents import agent


def test_safe_target_blocks_escape(tmp_path):
    (tmp_path / "src").mkdir()
    ok = agent._safe_target(tmp_path, "src/app/page.tsx")
    assert str(ok).startswith(str(tmp_path.resolve()))

    for evil in ["../secret", "../../etc/passwd", "src/../../out"]:
        with pytest.raises(ValueError):
            agent._safe_target(tmp_path, evil)


def test_is_within(tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    assert main._is_within(base / "a" / "b.txt", base) is True
    assert main._is_within(base / ".." / "x", base) is False
    assert main._is_within(tmp_path / "other", base) is False


def test_validated_upload_path(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)

    good = tmp_path / "sketch_abc.png"
    good.write_bytes(b"\x89PNG\r\n")
    assert main._validated_upload_path(str(good)) == str(good.resolve())
    assert main._validated_upload_path(None) is None
    assert main._validated_upload_path("") is None

    # A path outside the upload dir is rejected (arbitrary-file-read guard).
    with pytest.raises(main.HTTPException) as ei:
        main._validated_upload_path(str(tmp_path.parent / "secret.env"))
    assert ei.value.status_code == 400

    # Traversal that resolves outside the upload dir is rejected.
    with pytest.raises(main.HTTPException):
        main._validated_upload_path(str(tmp_path / ".." / "etc" / "passwd"))

    # Inside the upload dir but the file doesn't exist.
    with pytest.raises(main.HTTPException):
        main._validated_upload_path(str(tmp_path / "missing.png"))


def test_require_job_owner_rejects_other_session(tmp_path):
    import jobs
    jobs.init_db()
    job_id = jobs.create_job("m", None, None, None, session_id="owner-sess")

    class Req:
        def __init__(self, cookie):
            self.cookies = {main.SESSION_COOKIE_NAME: cookie} if cookie else {}

    assert main._require_job_owner(job_id, Req("owner-sess"))["id"] == job_id
    with pytest.raises(main.HTTPException) as ei:
        main._require_job_owner(job_id, Req("intruder"))
    assert ei.value.status_code == 404
    with pytest.raises(main.HTTPException):
        main._require_job_owner(job_id, Req(None))


def test_antigravity_sdk_configs_and_policies(tmp_path):
    from google.antigravity import LocalAgentConfig, types as agy_types

    # Verify all 5 build stages + refine_agent produce valid Antigravity LocalAgentConfig & SubagentConfig
    assert [s.name for s in agent.build_agent.sub_agents] == [
        "vision_agent",
        "planner_agent",
        "ui_agent",
        "backend_agent",
        "testing_agent",
    ]
    sub_configs = agent.build_agent.to_subagent_configs(tmp_path)
    assert len(sub_configs) == 5
    assert all(isinstance(sc, agy_types.SubagentConfig) for sc in sub_configs)

    local_cfg = agent.testing_agent.to_local_agent_config(tmp_path)
    assert isinstance(local_cfg, LocalAgentConfig)
    assert str(tmp_path.resolve()) in local_cfg.workspaces
    assert local_cfg.budget_config is not None
    assert local_cfg.budget_config.max_tool_calls == 40

    # Verify governance policy blocks destructive / direct-deploy shell commands
    assert agent._is_forbidden_command(
        agy_types.ToolCall(name="run_workspace_command", args={"command": "gcloud run deploy my-svc"})
    ) is True
    assert agent._is_forbidden_command(
        agy_types.ToolCall(name="run_workspace_command", args={"command": "sudo rm -rf /"})
    ) is True
    assert agent._is_forbidden_command(
        agy_types.ToolCall(name="run_workspace_command", args={"command": "npm run build"})
    ) is False


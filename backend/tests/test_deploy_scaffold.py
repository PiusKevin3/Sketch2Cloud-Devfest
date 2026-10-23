import json

import deploy_scaffold


def _pkg(tmp_path, scripts=None):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "gen", "scripts": scripts or {}}), encoding="utf-8"
    )


def test_next_plus_fastapi(tmp_path):
    _pkg(tmp_path, {"dev": "next dev"})
    (tmp_path / "next.config.ts").write_text("export default {};", encoding="utf-8")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "main.py").write_text("app = 1", encoding="utf-8")

    written = deploy_scaffold.write_deploy_scaffold(tmp_path)

    assert {"Dockerfile", "start.sh", ".gcloudignore", ".dockerignore", "next.config.mjs"} <= set(written)
    assert not (tmp_path / "next.config.ts").exists()          # boilerplate config replaced
    assert "127.0.0.1:8000" in (tmp_path / "next.config.mjs").read_text()
    assert "node_modules" in (tmp_path / ".gcloudignore").read_text()
    assert (tmp_path / "api" / "requirements.txt").exists()    # synthesised when missing
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["scripts"]["build"] == "next build"
    assert pkg["scripts"]["start"] == "next start"
    assert b"\r" not in (tmp_path / "start.sh").read_bytes()   # LF for the container


def test_next_only_leaves_user_config_alone(tmp_path):
    _pkg(tmp_path, {"build": "next build", "start": "next start"})
    (tmp_path / "next.config.js").write_text("// user config\nmodule.exports = {};", encoding="utf-8")

    written = deploy_scaffold.write_deploy_scaffold(tmp_path)

    assert "next.config.mjs" not in written
    assert not (tmp_path / "next.config.mjs").exists()
    assert (tmp_path / "next.config.js").read_text().startswith("// user config")


def test_noop_without_package_json(tmp_path):
    assert deploy_scaffold.write_deploy_scaffold(tmp_path) == []

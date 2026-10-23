import jobs


def setup_module(_):
    jobs.init_db()


def test_event_log_roundtrip_and_status():
    job_id = jobs.create_job("msg", None, "proj", "us-central1", session_id="sess-1")
    assert jobs.get_job(job_id)["status"] == "running"

    s1 = jobs.append_event(job_id, {"author": "vision_agent", "content": {"parts": [{"text": "hi"}]}})
    s2 = jobs.append_event(job_id, {"author": "system", "control": "done"})
    assert (s1, s2) == (1, 2)

    after0 = jobs.get_events_after(job_id, 0)
    assert [seq for seq, _ in after0] == [1, 2]
    assert jobs.get_events_after(job_id, 1) == [(2, {"author": "system", "control": "done"})]

    jobs.set_status(job_id, "success", result={"url": "https://x"})
    job = jobs.get_job(job_id)
    assert job["status"] == "success"
    assert job["result"] == {"url": "https://x"}


def test_latest_job_for_session_is_scoped():
    a = jobs.create_job("a", None, None, None, session_id="owner")
    b = jobs.create_job("b", None, None, None, session_id="owner")
    jobs.create_job("c", None, None, None, session_id="someone-else")

    latest = jobs.get_latest_job_for_session("owner")
    assert latest["id"] in {a, b}
    assert jobs.get_latest_job_for_session("nobody") is None


def test_reconcile_orphaned_jobs():
    running = jobs.create_job("r", None, None, None, session_id="s")
    deploying = jobs.create_job("d", None, None, None, session_id="s")
    jobs.set_status(deploying, "deploying")
    waiting = jobs.create_job("w", None, None, None, session_id="s")
    jobs.set_status(waiting, "awaiting_approval")

    n = jobs.reconcile_orphaned_jobs()
    assert n >= 2

    assert jobs.get_job(running)["status"] == "error"
    assert jobs.get_job(deploying)["status"] == "error"
    # a human-gated job is NOT an orphan
    assert jobs.get_job(waiting)["status"] == "awaiting_approval"

    tail = jobs.get_events_after(running, 0)
    assert any(e.get("control") == "error" for _, e in tail)
    assert any(e.get("control") == "done" for _, e in tail)

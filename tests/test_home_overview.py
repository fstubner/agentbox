"""The landing page, which introduces the assistant before showing anything
that needs a decision."""
from __future__ import annotations

import sys

import pytest
from test_portal_auth import load_portal


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_POLICY_DIR", str(tmp_path / "policy"))
    module = load_portal(tmp_path, monkeypatch)
    # Nothing here should shell out to docker or probe eight ports.
    monkeypatch.setattr(module.agentbox_status, "cached",
                        lambda: module.agentbox_status.Snapshot(taken_at=0))
    return module


def test_a_new_person_is_told_what_this_is(portal):
    """A person with nothing stored and nothing waiting is treated as new."""
    body = portal.render_overview("sam", portal.MEMBER, waiting=0,
                                  has_memories=False)
    assert "What this is" in body
    assert "only the ones you approve" in body


def test_somebody_with_memories_is_not_reintroduced(portal):
    """A household member who already uses the assistant does not need the
    introduction again."""
    body = portal.render_overview("alex", portal.MEMBER, waiting=0,
                                  has_memories=True)
    assert "What this is" not in body


def test_waiting_memories_are_surfaced_before_the_queue(portal):
    body = portal.render_overview("alex", portal.MEMBER, waiting=2,
                                  has_memories=True)
    assert "2 memories waiting" in body
    single = portal.render_overview("alex", portal.MEMBER, waiting=1,
                                    has_memories=True)
    assert "1 memory waiting" in single


def test_a_person_with_nothing_connected_is_pointed_at_accounts(portal):
    body = portal.render_overview("sam", portal.MEMBER, waiting=0,
                                  has_memories=False)
    assert "No accounts connected yet" in body
    assert "/connectors" in body


def test_only_admins_are_shown_the_box(portal):
    """Members do not need service health, and computing it costs a docker
    call and eight probes."""
    member = portal.render_overview("sam", portal.MEMBER, waiting=0,
                                    has_memories=True)
    admin = portal.render_overview("alex", portal.ADMIN, waiting=0,
                                   has_memories=True)
    assert "/admin" not in member
    assert "/admin" in admin


def test_an_evaluation_alone_is_not_alarming(portal, monkeypatch):
    """An evaluation with nothing wrong does not show the page as failing, and
    an evaluation never hides a real failure.
    """
    status = portal.agentbox_status
    monkeypatch.setattr(status, "cached", lambda: status.Snapshot(
        taken_at=0, evaluating=True))
    body = portal.render_overview("alex", portal.ADMIN, waiting=0,
                                  has_memories=True)
    assert "Everything is running" in body
    assert "evaluation in progress" in body


def test_a_real_outage_still_says_so(portal, monkeypatch):
    status = portal.agentbox_status
    monkeypatch.setattr(status, "cached", lambda: status.Snapshot(
        taken_at=0, evaluating=False,
        endpoints=[status.Check("main model", False, "", status.FAIL)]))
    body = portal.render_overview("alex", portal.ADMIN, waiting=0,
                                  has_memories=True)
    assert "1 service(s) down" in body


# --- the settings prose --------------------------------------------------------


def test_every_setting_has_a_one_line_hint(portal):
    """Every setting has a short hint, with the full reasoning folded away. A
    setting without one would show the long text instead."""
    settings = sys.modules["agentbox_settings"]
    missing = [s.key for s in settings.SETTINGS if not s.hint]
    assert missing == [], missing
    for setting in settings.SETTINGS:
        assert len(setting.hint) < 90, setting.key


def test_the_rationale_is_still_on_the_page_just_folded(portal):
    """Removing it would lose the record of why each setting is as it is."""
    body = portal.render_admin("alex", "").decode()
    assert "<details" in body
    assert "Why</summary>" in body
    settings = sys.modules["agentbox_settings"]
    admins = settings.BY_KEY["admins"]
    assert admins.hint in body
    # Any sentence from the long form will do. Pinning a whole exact phrase
    # would fail whenever the admins rationale is reworded.
    assert "manages only their own" in body


def test_an_evaluation_never_hides_a_real_problem(portal, monkeypatch):
    """A running benchmark must not hide problems on the status card.

    A certification run does not stop production. Treating an evaluation as a
    planned stop would hide real outages on the page that exists to show
    them.
    """
    status = portal.agentbox_status
    monkeypatch.setattr(status, "cached", lambda: status.Snapshot(
        taken_at=0, evaluating=True,
        other=[status.Check("Disk", False, "97% used", status.FAIL)]))
    body = portal.render_status_card()
    assert "Disk" in body and "97% used" in body
    assert "needs attention" in body


def test_a_healthy_box_during_an_evaluation_reads_as_healthy(portal, monkeypatch):
    """An evaluation explains problems; it does not invent them."""
    status = portal.agentbox_status
    monkeypatch.setattr(status, "cached", lambda: status.Snapshot(
        taken_at=0, evaluating=True))
    body = portal.render_status_card()
    assert "Everything is running" in body
    assert "Paused for an evaluation" not in body


def test_the_home_page_does_not_hide_an_outage_behind_an_evaluation(portal,
                                                                    monkeypatch):
    """The same applies to the home page's status row, which people see first.
    """
    status = portal.agentbox_status
    monkeypatch.setattr(status, "cached", lambda: status.Snapshot(
        taken_at=0, evaluating=True,
        endpoints=[status.Check("main model", False, "", status.FAIL)]))
    body = portal.render_overview("alex", portal.ADMIN, waiting=0,
                                  has_memories=True)
    assert "1 service(s) down" in body
    assert "Paused for an evaluation" not in body

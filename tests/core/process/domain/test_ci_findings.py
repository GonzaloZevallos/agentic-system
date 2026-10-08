"""`ci.ci_findings`: the system's own findings for a declared CI check that is red on
the head under review (GH-216). The reviewer never writes these; the code does."""

from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, ci_findings

HEAD_LINK = "https://github.com/o/r/actions/runs/1/job/10"
MASTER_LINK = "https://github.com/o/r/actions/runs/2/job/20"


def run(name, conclusion="SUCCESS", link=HEAD_LINK, status="COMPLETED"):
    return CiCheck(name=name, status=status, conclusion=conclusion, link=link)


def report(*checks, sha="a" * 40):
    return CiReport(
        head_sha=sha, base_ref="master", base_sha="b" * 40, opened_at=None, checks=tuple(checks)
    )


def quoted(link):
    return {
        "location": "web/x.ts:3",
        "claim": "the e2e job fails",
        "evidence": f"see {link}",
        "severity": "blocking",
        "scope": "subtask",
        "category": "process-suite-red",
    }


def test_red_on_the_head_and_green_on_master_is_a_blocking_finding():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    master = report(run("e2e", "SUCCESS", MASTER_LINK))
    [added] = ci_findings(head, master, ("e2e",), [])
    assert added["severity"] == "blocking"
    assert added["scope"] == "subtask"
    assert added["category"] == "process-suite-red"
    assert added["location"] == "CI: e2e"
    assert HEAD_LINK in added["evidence"]


def test_red_on_both_is_an_observation_that_names_both_links():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    master = report(run("e2e", "TIMED_OUT", MASTER_LINK))
    [added] = ci_findings(head, master, ("e2e",), [])
    assert added["severity"] == "observation"
    assert added["category"] == "process-suite-red"
    assert added["location"] == "CI: e2e"
    assert HEAD_LINK in added["evidence"] and MASTER_LINK in added["evidence"]


def test_a_link_the_reviewer_already_quoted_adds_no_duplicate():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    existing = [quoted(HEAD_LINK)]
    assert ci_findings(head, report(run("e2e")), ("e2e",), existing) == existing


def test_a_green_check_adds_nothing():
    head = report(run("e2e", "SUCCESS"))
    assert ci_findings(head, report(run("e2e")), ("e2e",), []) == []


def test_an_unreadable_master_leaves_the_red_check_blocking():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    [added] = ci_findings(head, CiUnreadable("gh_unreadable"), ("e2e",), [])
    assert added["severity"] == "blocking"
    assert added["evidence"] == HEAD_LINK


def test_no_master_report_leaves_the_red_check_blocking():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    [added] = ci_findings(head, None, ("e2e",), [])
    assert added["severity"] == "blocking"


def test_an_undeclared_red_check_is_ignored_and_the_input_list_is_not_mutated():
    head = report(run("lint", "FAILURE"), run("e2e", "FAILURE", "https://x/e2e"))
    findings: list[dict] = []
    added = ci_findings(head, None, ("e2e",), findings)
    assert [f["location"] for f in added] == ["CI: e2e"]
    assert findings == []


def test_the_first_red_run_of_a_name_supplies_the_link():
    head = report(
        run("e2e", "SUCCESS", "https://x/ok"),
        run("e2e", "FAILURE", "https://x/first-red"),
        run("e2e", "FAILURE", "https://x/second-red"),
    )
    [added] = ci_findings(head, None, ("e2e",), [])
    assert added["evidence"] == "https://x/first-red"


def test_a_longer_job_id_that_starts_with_the_head_link_does_not_hide_it():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    [kept, added] = ci_findings(head, None, ("e2e",), [quoted(HEAD_LINK + "0")])
    assert added["severity"] == "blocking" and added["evidence"] == HEAD_LINK


def test_a_base_branch_link_the_reviewer_quoted_is_not_repeated_in_the_observation():
    head = report(run("e2e", "FAILURE", HEAD_LINK))
    master = report(run("e2e", "FAILURE", MASTER_LINK))
    [_, added] = ci_findings(head, master, ("e2e",), [quoted(MASTER_LINK)])
    assert added["severity"] == "observation"
    assert HEAD_LINK in added["evidence"] and MASTER_LINK not in added["evidence"]

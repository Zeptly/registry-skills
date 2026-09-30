"""Guards against documentation drifting from the implemented schemas and behaviour."""
import re

from conftest import REPO

DOCS = [*(REPO / "docs").glob("*.md"), REPO / "README.md", REPO / "CONTRIBUTING.md", REPO / "SECURITY.md",
        REPO / ".github" / "pull_request_template.md"]
# (pattern, files where a historical/explanatory mention is legitimate)
STALE = [
    (r"zsk\.", {"PROTOCOL-ALIGNMENT.md", "SPECIFICATION.md"}),                # legacy prefix: migration / rejection notes only
    (r"skill_ref|skill_digest|suite_digest", {"PROTOCOL-ALIGNMENT.md"}),        # replaced by structured `subject` / `suiteDigest`
    (r"basis: eval-report|basis: waiver", set()),                              # now `evaluation` / `protocol-exception`
    (r"agentgit://|zep://", set()),                                            # pointers are evidence:// or https:// only
    (r"security_reviewed_by", set()),                                          # now securityReviewedBy
    (r"evidence_level", set()),                                                # now evidenceLevel
    (r"`imported`|`authored`", {"PROTOCOL-ALIGNMENT.md"}),                     # origin values: native | upstream-seed | discovered | refined | evolved
    (r"ResolutionLock|resolution-lock", {"PROTOCOL-ALIGNMENT.md"}),            # now RuntimeLock / runtime-lock.schema.json
    (r"markers\.provenance|markers: \{provenance", {"PROTOCOL-ALIGNMENT.md", "OPEN-DECISIONS.md"}),  # replaced by origin.type: discovered
    (r"CRLF and lone CR become LF|normali[sz]ed bytes", set()),               # payload is rejected, never normalized
    (r"test_digest_vectors", {"PROTOCOL-ALIGNMENT.md"}),                                          # replaced by tests/test_vectors.py
    (r"\btrust_tier\b", set()),                                                # now spec.trust.tier
]


def test_docs_do_not_mention_removed_fields_and_conventions():
    problems = []
    for p in DOCS:
        text = p.read_text(encoding="utf-8")
        for pattern, allowed in STALE:
            if p.name in allowed:
                continue
            for m in re.finditer(pattern, text):
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{p.relative_to(REPO)}:{line}: stale term {m.group(0)!r}")
    assert not problems, "\n".join(problems)


def test_documented_exit_codes_and_guarantees_exist():
    spec = (REPO / "docs" / "SPECIFICATION.md").read_text()
    for needle in ("Parser rejection", "Unhashable bundles fail loudly", "Index generation never skips",
                   "schema-validated before success", "Exit codes", "zeptly-jcs-v1", "Skills-generated vectors"):
        assert needle in spec

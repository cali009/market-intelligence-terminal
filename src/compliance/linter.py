"""
Impersonal Advice Linter
Automated enforcement of CSA Staff Notice 31-369 and SEC Publisher Exclusion boundaries.
Scans text, markdown, prompts, and UI copy for advisory phrasing violations.
"""

import re
from typing import List, Dict, Any, NamedTuple


class LintViolation(NamedTuple):
    line_number: int
    matched_phrase: str
    rule_category: str
    context: str
    remediation_suggestion: str


# Disallowed phrases and their remediation suggestions
BANNED_ADVISORY_PATTERNS = [
    (
        r"\b(?:you\s+should\s+(?:buy|sell|short|accumulate|dump))\b",
        "DIRECT_INSTRUCTION",
        "Never instruct the user. Replace with quantitative ranking: "
        "'The model assigns an opportunity score of X/100 based on factor Y.'",
    ),
    (
        r"\b(?:we\s+recommend(?:\s+buying|\s+selling)?)\b",
        "RECOMMENDATION_CLAIM",
        "Do not issue recommendations. State factual metrics: "
        "'The technical breakout scanner triggered with RVOL of 2.1x.'",
    ),
    (
        r"\b(?:our\s+advice\s+is|financial\s+advice|personalized\s+advice)\b",
        "ADVICE_CLAIM",
        "Never characterize output as financial advice. "
        "Reference 'impersonal quantitative decision-support research'.",
    ),
    (
        r"\b(?:guaranteed\s+(?:return|profit|gain)|risk-free|can't\s+lose)\b",
        "PROMISSORY_CLAIM",
        "Strictly prohibited. All market analysis must state risks and potential loss.",
    ),
    (
        r"\b(?:recommended\s+portfolio\s+allocation|allocate\s+\d+%\s+of\s+your\s+portfolio)\b",
        "PORTFOLIO_TAILORING",
        "Do not prescribe personalized allocation sizes. "
        "State standardized risk sizing formulas: 'At a 1% risk budget, position size equates to N shares.'",
    ),
    (
        r"\b(?:certainty\s+of\s+success|sure\s+thing|lock\s+of\s+the\s+week)\b",
        "PROMISSORY_CLAIM",
        "Never imply certainty. Present probability estimates with sample sizes and historical confidence bounds.",
    ),
    (
        r"\b(?:strong\s+buy\s+rating|strong\s+sell\s+rating)\b",
        "ANALYST_RATING_PHRASING",
        "Avoid traditional broker rating jargon. Use 'Grade A (Score 80-100)' or 'Top Decile Quantitative Rank'.",
    ),
]


class ImpersonalAdviceLinter:
    def __init__(self):
        self.compiled_rules = [
            (re.compile(pattern, re.IGNORECASE), category, suggestion)
            for pattern, category, suggestion in BANNED_ADVISORY_PATTERNS
        ]

    def lint_text(self, text: str) -> List[LintViolation]:
        """
        Scan a block of text and return all detected advisory phrasing violations.
        """
        violations = []
        lines = text.split("\n")

        for line_idx, line in enumerate(lines, start=1):
            for regex, category, suggestion in self.compiled_rules:
                matches = regex.finditer(line)
                for match in matches:
                    violations.append(
                        LintViolation(
                            line_number=line_idx,
                            matched_phrase=match.group(0),
                            rule_category=category,
                            context=line.strip(),
                            remediation_suggestion=suggestion,
                        )
                    )
        return violations

    def assert_clean(self, text: str, source_label: str = "Input") -> None:
        """
        Asserts that text contains zero advisory violations; raises ValueError otherwise.
        """
        violations = self.lint_text(text)
        if violations:
            msg_lines = [
                f"Compliance Linter Failed for {source_label}: {len(violations)} violation(s) found:"
            ]
            for v in violations:
                msg_lines.append(
                    f"  Line {v.line_number} [{v.rule_category}]: '{v.matched_phrase}'"
                )
                msg_lines.append(f"    Context: {v.context}")
                msg_lines.append(f"    Fix: {v.remediation_suggestion}")
            raise ValueError("\n".join(msg_lines))


# Global singleton instance
linter = ImpersonalAdviceLinter()

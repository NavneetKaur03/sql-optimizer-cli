# analyzer.py
# Runs the query through the parser and checks all the rules for  a combined list of findings.

import sys

from sql_analyzer.parser import QueryParser, Finding
from sql_analyzer.rules import MissingIndexRule, CartesianJoinRule, NonSARGableRule, UnusedCTERule
from typing import List

class Analyzer:
    def __init__(self):
        self.parser = QueryParser()
        self.rules = [
            MissingIndexRule(),
            CartesianJoinRule(),
            NonSARGableRule(),
            UnusedCTERule()
        ]

    def analyze(self, query: str, conn) -> List[Finding]:
        """
        Returns a flat list of all findings across all rules.
        Empty list means no issues detected.
        """

        parsed = self.parser.parse(query)
        all_findings = []

        for rule in self.rules:
            findings = rule.detect(parsed, conn) or []
            all_findings.extend(findings)
        
        return all_findings
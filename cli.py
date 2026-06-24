# cli.py
# Entry point of SQL Analyzer tool
# Accepts raw SQL query and output arguments, calls the Analyzer and prints all the findings.

import argparse
import json
import sys
import psycopg2

from sql_analyzer.analyzer import Analyzer

# --- DB CONFIG ---
from sql_analyzer.config import DB_CONFIG

SEVERITY_ORDER = {"HIGH" : 0, "MEDIUM" : 1, "LOW" : 2}

def get_connection():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        # Operational error means db not reachable due to incorrect port, password or 
        # container not running.
        print(f"Could not connect to PostgreSQL: {e}")
        print("Is your Docker container running? Try docker start sql-analyzer-pg")
        sys.exit(1) # exit code 0 means success other-wise failure.

def print_text(findings) -> None:
    """
    Prints findings in a more readable format.
    Sorted by severity from HIGH to MEDIUM to LOW.
    """
    if not findings:
        print("No issues detected. Query looks good.")
        return
    
    sorted_findings = sorted(
        findings,
        key = lambda f: SEVERITY_ORDER.get(f.severity, 99)
        # default to 99 if severity is unknown
    )

    print(f"\nFound {len(findings)} issues:\n")
    print("-" * 50)

    for i, finding in enumerate(sorted_findings, start=1):
        print(f"{i}. [{finding.severity}] {finding.rule_name}")
        print(f"Issue:  {finding.message}")
        print(f"Suggestion: {finding.suggestion}")
        print("-"*50)

def print_json(findings) -> None:
    """
    Prints findings as a JSON array.
    We manually convert each finding to a dict using __dict__ 
    to get it to auto-serialize to JSON.
    """
    output = [finding.__dict__ for finding in findings]
    print(json.dumps(output, indent=2))

def main():
    parser = argparse.ArgumentParser(
        description="SQL Query Optimization Analyzer - detects performance issues in queries."
    )

    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The SQL query to analyze. Wrap in double quotes."
    )

    parser.add_argument(
        "--output",
        type=str,
        choices=["text","json"],
        default="text",
        help="Output format: 'text' (default)  or 'json'"
    )

    args = parser.parse_args()

    conn = get_connection()

    try:
        analyzer = Analyzer()
        findings = analyzer.analyze(args.query, conn)
        
        if args.output == 'json':
            print_json(findings)
        else:
            print_text(findings)

    finally:
        conn.close()

if __name__ == "__main__":
    main()



# rules.py
# Contains a base rule class and four detection rules.
# Each rule gets a ParssedQuery and returns its findings.

import re
import psycopg2
from abc import ABC, abstractmethod
from typing import List, Dict

import sys
from sql_analyzer.parser import ParsedQuery, Finding

# --- BASE CLASS ---
class Rule(ABC):

    @abstractmethod
    def detect(self, parsed: ParsedQuery, conn) -> List[Finding]:
        pass

#Rule 1: Missing Index Rule
#Working: Run EXPLAIN ANALYZE. If Index Scan exists, no issue; 
#else if Seq. Scan found, check column cardinality using pg_stats. 
#Only columns with high cardinality are beneficial for indexing.

class MissingIndexRule(Rule):
    # Distinct values in a column should be more than CARDINALITY_THRESHOLD 
    # to benefit from a B-Tree index.
    CARDINALITY_THRESHOLD = 50

    def detect(self, parsed: ParsedQuery, conn) -> List[Finding]:
        findings = []

        columns_to_check = set(parsed.where_columns + parsed.join_columns)

        if not columns_to_check or not parsed.tables:
            return []
        
        # Step 1: Run EXPLAIN ANALYZE on the original query.
        # PostgreSQL will actually run the query and tell us what is the current scan type.
        explain_output = self._run_explain(parsed.original_query, conn)
        if explain_output is None:
            return []
        
        # If Index Scan is already being used, then no need to flag
        has_seq_scan = 'Seq Scan' in explain_output
        if not has_seq_scan:
            return []
        
        # Step 2: Seq Scan found, now checking for columns which are unindexed 
        # and have a high cardinality to benefit from indexing.
        indexed_columns = self._get_indexed_columns(parsed.tables, conn)
        column_table_map = self._get_column_table_map(parsed.tables, conn)
        primary_keys = self._get_primary_keys(parsed.tables, conn)

        for col in columns_to_check:
            if col.lower() in indexed_columns:
                continue

            table_for_col = column_table_map.get(col.lower())
            if table_for_col is None: 
                continue
                
            cardinality = self._get_cardinality(table_for_col, col, conn)

            if cardinality is None:
                continue
            
            # Step 3: Deriving cardinality to identify whether the column should be indexed or not by comparing it with the threshold value
            if cardinality < 0:
                row_count = self._get_row_count(table_for_col, conn)
                actual_distinct = abs(cardinality)*row_count
            else:
                actual_distinct = cardinality

            if actual_distinct < self.CARDINALITY_THRESHOLD:
                continue

            findings.append(
                Finding(
                    rule_name = "MissingIndexRule",
                    severity = "HIGH",
                    message = (
                        f"Column {col} on table {table_for_col} has no index and"
                        f"~{int(actual_distinct):,} distinct values." 
                        f"PostgreSQL is using a sequential scan and using an index would speed up this query."
                    ),
                    suggestion = f"CREATE INDEX ON {table_for_col}({col});"
                )
            )

        return findings
    
    # Helper function for getting details on columns and corressponding tables
    def _get_column_table_map(self, tables: List[str], conn) -> Dict:
        """
        Queries information_schema.columns to find which table each column belongs to.
        If a column exists in multiple tables then we keep the last one found.
        """

        if not tables:
            return {}
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, table_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name IN %s""", (tuple(tables),))
            
            return {
                col.lower() : tbl.lower()
                for col, tbl in cur.fetchall()
            }
        
    def _get_primary_keys(self, tables: List[str], conn) -> set:
        """
        Returns primary key column names for given tables.
        Primary keys are always indexed — flagging them would be a false positive.
        """
        if not tables:
            return set()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = 'public'
                  AND tc.table_name IN %s
            """, (tuple(tables),))
            return {row[0].lower() for row in cur.fetchall()}

    def _run_explain(self, query: str, conn) -> str | None:
        """
        Runs EXPLAIN ANALYZE on the original query and returns full output as a string.
        Returns None in case of any failures (syntax errors, missing tables etc.)
        """
        stripped = query.strip().upper()
        if not stripped.startswith('SELECT'):
            # We only analyse SELECT queries — DML is out of scope
            return None

        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN {query}")
                rows = cur.fetchall()
                return '\n'.join(row[0] for row in rows)
        except psycopg2.Error:
            conn.rollback()
            return None
        
    def _get_indexed_columns(self, tables: List[str], conn) -> set:
        # Returns a set of column names that already have indexes.

        if not tables:
            return set()
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT indexdef 
                FROM pg_indexes 
                WHERE schemaname = 'public'
                AND tablename IN %s""", (tuple(tables),))
        
            indexed = set()
            for (indexdef,) in cur.fetchall():
                # Extract column names from parantheses
                match = re.search(r'\((.+)\)', indexdef)
                if match:
                    # Handling composite indexes
                    cols = [c.strip().lower() for c in match.group(1).split(',')]
                    indexed.update(cols)
            return indexed
        
    def _get_cardinality(self, table: str, column: str, conn) -> float | None:
        """
        Fetches n_distinct from pg_stats for a table and column combination
        Statistics in the table is populated by running ANALYZE.
        Positive number → actual count of distinct values (eg; 4)
        Negative number → fraction of rows that are distinct (eg; -0.95 which means 95%)
        0               → statistics not yet collected (run ANALYZE first)
        """

        with conn.cursor() as cur:
            cur.execute("""
                SELECT n_distinct 
                FROM pg_stats
                WHERE schemaname = 'public'
                AND tablename = %s AND attname = %s""",
                (table.lower(), column.lower()))
            
            row = cur.fetchone()
            if row is None or row[0] == 0:
                return None
            return float(row[0])
    
    def _get_row_count(self, table:str, conn) -> int:
        """
        Gets approximate row count from reltuples attribute in pg_class.
        reltuples give the last known row count estimate updated by ANALYZE
        """
        with conn.cursor() as cur:
            cur.execute("""
                SELECT reltuples::bigint
                FROM pg_class
                WHERE relname = %s""", (table.lower(),))
            row = cur.fetchone()
            return int(row[0]) if row else 0
        
# Rule 2: Cartesian Join Rule
# Detects queries that use a cartesian join
# Identification done by spotting CROSS JOIN keyword 
# or multiple tables in FROM with no JOIN/ON condition

class CartesianJoinRule(Rule):

    def detect(self, parsed: ParsedQuery, conn) -> List[Finding]:
        findings = []

        # Case 1: explicit CROSS JOIN
        if parsed.join_type == 'CROSS':
            findings.append(Finding(
                rule_name = 'CartesianJoinRule',
                severity = "HIGH",
                message = f"CROSS JOIN detected. This will produce a cartesian product. "
                          f"With large tables, the complexity would reach up to O(n²).",
                suggestion = f"Replace CROSS JOIN with INNER or LEFT JOIN using an ON condition, "
                             f"Unless a Cartesian Join was intended."
            ))
        
        # Case 2: multiple tables in after FROM keyword with no JOIN condition (Old-style SQL)
        elif len(parsed.tables) > 1 and not parsed.has_join:
            findings.append(Finding(
                rule_name = "CartesianJoinRule",
                severity = "HIGH",
                message = f"Multiple tables ({','.join(parsed.tables)}) in FROM clause "
                          f"with no JOIN keyword. This is an implicit Cartesian Join.",
                suggestion = f"Use explicit JOIN....ON condition instead of comma separated tables."
            ))
        return findings

class NonSARGableRule(Rule):
    FUNCTIONS = ['UPPER', 'LOWER', 'YEAR', 'MONTH', 'DATE', 'TRIM', 'LENGTH', 'TO_CHAR', 'COALESCE']

    def detect(self, parsed: ParsedQuery, conn) -> List[Finding]:
        findings = []

        where_match = re.search(r'\bWHERE\b(.+?)(?:\b(?:ORDER BY|GROUP BY|LIMIT|HAVING)\b|$)',
                                parsed.original_query, re.IGNORECASE)
        
        if not where_match:
            return []
        
        where_clause = where_match.group(1)
         
        # Checking for standard fucntions
        for func in self.FUNCTIONS:
            pattern = rf'\b{func}\s*\('
            if where_clause and re.search(pattern, where_clause, re.IGNORECASE):
                findings.append(Finding(
                    rule_name = "NonSARGableRule",
                    severity = "HIGH",
                    message = (
                        f"Function {func}() in WHERE clause prevents index usage. "
                        f"PostgreSQL cannot use a B-Tree index on a function-wrapped column."
                    ),
                    suggestion = (
                        f"Store data in a consistent format to avoid using {func}(), "
                        f"or create a functional index: "
                        f"CREATE INDEX ON table({func.lower()}(column));"
                    )
                ))
        
        if where_clause is None:
            return []
        
        # Flag the keyword CAST only when it is enclosing a column and not a literal.
        cast_matches = re.finditer(
            r'\bCAST\s*\(\s*(\w+)\s+AS\b',
            where_clause,
            re.IGNORECASE
        )
        sql_keywords = {'null', 'true', 'false', 'current_date', 'current_timestamp'}

        for match in cast_matches:
            argument = match.group(1)
            # If argument is a number or sql keyword literal,, skip
            if argument.isdigit() or argument.lower() in sql_keywords:
                continue

            # Otherwise it is a column
            findings.append(Finding(
                rule_name = "NonSARGAableRule",
                severity = "HIGH",
                message = (
                    f"CAST({argument} AS ...) in WHERE clause prevents index usage. "
                    f"Casting a column breaks B-Tree Index lookups."
                ), 
                suggestion = (
                    f"Store the column in the target type in first place. "
                    f"or cast the comparision value instead: "
                    f"WHERE {argument} = CAST(value as original_type)."
                )
            ))
        
        # The keyword LIKE will be flagged as MEDIUM because sometimes its really necessary
        leading_wildcard = re.search(r"\bLIKE\s+'%[^']*'", where_clause, re.IGNORECASE)

        if leading_wildcard:
            findings.append(Finding(
                rule_name = "NonSARGableRule",
                severity = "MEDIUM",
                message = (
                    f"Leading wildcard LIKE '%value' prevents index usage. "
                    f"PostgreSQL will have to scan every row to check the suffix."
                ),
                suggestion = (
                    f"Use LIKE 'value%' for prefix search."
                    f"For suffix/full-text search consider pg_trgm extension or tsvector full-text search."
                )
            ))
        
        return findings

class UnusedCTERule(Rule):

    def detect(self, parsed: ParsedQuery, conn) -> List[Finding]:
        findings = []

        if not parsed.cte_names:
            return []
        
        query = parsed.original_query
        with_match = re.search(r'\bWITH\b', query, re.IGNORECASE)
        if not with_match:
            return []
        
        # Finding main SELECT using depth tracking
        pos = with_match.end()
        depth = 0
        main_select_pos = None

        while pos < len(query):
            char = query[pos]
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif depth == 0:
                if re.match(r'\bSELECT\b', query[pos:],  re.IGNORECASE):
                    main_select_pos = pos
                    break
            pos += 1
        
        if main_select_pos is None:
            return []
        
        # Only flag if the CTE name appears NOWHERE after its own definition

        cte_block = query[with_match.end():main_select_pos]
        main_body = query[main_select_pos:]

        for cte_name in parsed.cte_names:
            pattern = rf'\b{re.escape(cte_name)}\b'

            # Check in main body first
            body_after_select = (
                main_body[len('SELECT'):]
                if main_body.upper().startswith('SELECT')
                else main_body
            )

            used_in_main = bool(re.search(pattern, body_after_select, re.IGNORECASE))

            # Checking if its used in another CTE's definition
            cte_own_def = re.sub(
                rf'\b{re.escape(cte_name)}\s+AS\s*\(',
                '', cte_block, flags = re.IGNORECASE
            )

            used_in_another_cte =  bool(re.search(pattern, cte_own_def, re.IGNORECASE))

            if not used_in_main and not used_in_another_cte:
                findings.append(Finding(
                    rule_name = "UnusedCTERule",
                    severity = "LOW",
                    message = f"CTE '{cte_name}' is defined but never used in main query or other CTEs.",
                    suggestion = f"Remove the '{cte_name}' CTE  to keep the query clean and avoid unnecessary parsing."
                ))
        
        return findings
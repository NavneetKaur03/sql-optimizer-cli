# parser.py
# Responsible for taking a raw SQL string and pulling out information like tables, CTEs, columns, joins.
# Returns a parsed query as an output which is used by all the rules.

import re
from dataclasses import dataclass, field
from typing import List, Optional

# --- ParsedQuery : the structured output of the parser ---
@dataclass
class ParsedQuery:
    original_query: str                                       # Raw SQL input by user
    tables: List[str] = field(default_factory=list)           # Tables used in the query
    where_columns: List[str] = field(default_factory=list)    # Columns in WHERE clause
    join_columns: List[str] = field(default_factory=list)     # Columns in JOIN ON clause
    join_type: Optional[str] = None                           # "INNER", "LEFT", "CROSS" or None
    has_join: bool = False                                    # Set to True if a JOIN exists in the query
    cte_names: List[str] = field(default_factory=list)        # CTE names from WITH block      


# --- Finding : what each rule returns ---
@dataclass
class Finding:
    rule_name: str        # Which rule caught this     
    severity: str         # Conveys urgency of the fix and can be either of "HIGH", "MEDIUM" or "LOW"
    message: str          # Short description of the problem
    suggestion: str       # Plain English fix + DDL wherever applicable 

class QueryParser:
    """
    Parses the raw SQL into a ParsedQuery object.
    Uses Regex to extract the important parts required for rule detection.
    """

    def parse(self, query: str) -> ParsedQuery:
        # Strip leading/trailing whitespaces and collapse mutlitple whitespaces into one.
        clean = re.sub(r'\s+', ' ', query.strip())
        main_query = self._get_main_query(clean)

        return ParsedQuery(
            original_query=query,
            tables=self._extract_tables(clean),
            where_columns=self._extract_where_columns(clean),
            join_columns=self._extract_join_columns(main_query),
            join_type=self._extract_join_type(main_query),
            has_join=bool(re.search(r'\bJOIN\b', main_query, re.IGNORECASE)), # IGNORECASE handles both uppercase and lowercase SQL
            cte_names=self._extract_cte_names(clean)
        )
 
    def _get_main_query(self, query: str) -> str:
        """
        Returns only main query body after excluding CTE definitions.
        Prevents picking up column and tables names from CTEs which belong to subqueries.
        If no CTEs present, return full query as is.
        """
        with_match = re.search(r'\bWITH\b', query, re.IGNORECASE)
        if not with_match:
            return query

        # Depth tracking to find position of main SELECT
        pos = with_match.end()
        depth = 0

        while pos < len(query):
            if query[pos] == '(':
                depth += 1
            elif query[pos] == ')':
                depth -= 1
            elif depth == 0:
                if re.match(r'\bSELECT\b', query[pos:], re.IGNORECASE):
                    # Everything from here onwards is main query
                    return query[pos:]
            pos += 1
        
        return query
    
    def _extract_tables(self, query: str) -> List[str]:
        """
        Extracts table names from main query only, not from inside CTEs.
        Handles extraction from comma separated tables too.
        Also deduplicates so the same table appearing twice isn't listed twice.
        """
        main_query = self._get_main_query(query)

        tables = []

        pattern = r'\b(?:FROM|JOIN)\s+(\w+)'
        tables.extend(re.findall(pattern, main_query, re.IGNORECASE))

        from_match = re.search(r'\bFROM\s+\w+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bORDER\b|\bLIMIT\b|\bJOIN\b|$)',
                               main_query, re.IGNORECASE)
        if from_match:
            after_first_table = from_match.group(1)
            comma_tables = re.findall(r',\s*(\w+)', after_first_table)
            tables.extend(comma_tables)

        keywords = {'select', 'where', 'on', 'and', 'or', 'not'}

        return list(dict.fromkeys(
            t for t in tables if t.lower() not in keywords
        ))
    
    def _extract_where_columns(self, query: str) -> List[str]:
        main_query = self._get_main_query(query)

        # Guard: if _get_main_query returns None, return empty list
        # This happens when the query structure is unexpected
        if main_query is None:
            return []

        where_match = re.search(
            r'\bWHERE\b(.+?)(?:\b(?:ORDER BY|GROUP BY|LIMIT|HAVING|JOIN)\b|$)',
            main_query,
            re.IGNORECASE
        )
        if not where_match:
            return []

        where_clause = where_match.group(1)

        # Guard: group(1) should always be a string if match succeeded,
        # but defensive check prevents NoneType crash
        if where_clause is None:
            return []

        where_clause = self._strip_string_literals(where_clause)
        where_clause = re.sub(r'\b\w+\.', '', where_clause)

        tokens = re.findall(r'\b(\w+)\b', where_clause)
        sql_keywords = {
            'and', 'or', 'not', 'in', 'is', 'null', 'like',
            'between', 'exists', 'true', 'false', 'upper', 'lower',
            'year', 'month', 'day', 'trim', 'cast', 'coalesce'
        }
        return [
            t for t in tokens
            if t.lower() not in sql_keywords and not t.isdigit()
        ]

    def _strip_string_literals(self, text: str) -> List[str]:
        # Removes single-quoted string values from SQL text
        return re.sub(r"'[^']*'", '', text)

    def _extract_join_columns(self, query: str) -> List[str]:
        """
        Extract columns from JOIN ON clauses.
        Working: 
            The pattern being looked for here is ON word = word
            Words on both side of equality will be considered because either of them could be unindexed
        """

        pattern = r'\bON\b\s+(?:\w+\.)?(\w+)\s*=\s*(?:\w+\.)?(\w+)'
        matches = re.findall(pattern, query, re.IGNORECASE)

        return [col for pair in matches for col in pair]
    
    def _extract_join_type(self, query: str) -> List[str]:
        """
        Detects the type of join used.
        Returns "CROSS", "LEFT", "RIGHT", "INNER" or None.
        """

        for join_type in ['CROSS', 'LEFT', 'RIGHT', 'INNER']:
            if re.search(rf'\b{join_type}\b', query, re.IGNORECASE):
                return join_type
        
        if re.search(r'\bJOIN\b', query, re.IGNORECASE):
            return 'INNER'
            
        return None
    
    def _extract_cte_names(self, query: str) -> List[str]:
        """
        Extract CTE names from WITH blocks.

        Approach: 
        Find the position of the main SELECT by tracking paranthesis depth.
        The main SELECT is at depth 0, not nested inside any CTE's parathesis.
        """
        with_match = re.search(r'\bWITH\b', query, re.IGNORECASE)
        if not with_match:
            return []
        
        # Tracking parenthesis depth to find where the main SELECT starts
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
                # At depth 0, we would reach the main SELECT
                remaining = query[pos:]
                if re.match(r'\bSELECT\b', remaining, re.IGNORECASE):
                    main_select_pos = pos
                    break

            pos += 1

        if main_select_pos is None:
            return []

        cte_block = query[with_match.end():main_select_pos]
        cte_names = re.findall(r'(\w+)\s+AS\s*\(', cte_block, re.IGNORECASE)
        return cte_names
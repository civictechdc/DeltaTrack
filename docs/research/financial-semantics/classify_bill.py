import re

# Node-opening patterns
RESTRICT = re.compile(r"^\s*(?:\([a-z0-9]+\)\s*)?None of the funds", re.IGNORECASE)
RESTRICT_NOTWITHSTANDING = re.compile(r"^\s*Notwithstanding\b.{0,200}\bnone of the funds\b", re.IGNORECASE | re.DOTALL)
TRANSFER = re.compile(r"^\s*Of (?:the )?amounts.{0,300}\btransferr?ed?\b", re.IGNORECASE | re.DOTALL)
APPROP = re.compile(r"^\s*(?:\([a-z0-9]+\)\s*)?For\b", re.IGNORECASE | re.DOTALL)
RESCISSION = re.compile(r"(?:is|are) hereby rescinded", re.IGNORECASE)
DIRECTIVE = re.compile(r"^\s*The\s+\w[\w\s]+(?:shall|may not)\b", re.IGNORECASE)
REPROGRAM = re.compile(r"^\s*no project may be (?:increased|decreased)", re.IGNORECASE)
DELAYED_APPROP = re.compile(r"^\s*\$[\d,]+.{0,50}\bshall become available\b", re.IGNORECASE | re.DOTALL)
APPROP_ALT = re.compile(r"there (?:is|are)(?: hereby)? appropriated", re.IGNORECASE)
AUTHORIZATION = re.compile(r"\bauthorized to be appropriated\b", re.IGNORECASE)
FEE = re.compile(r"fee in the amount of\s+\$|impose a fee|\bpays a fee of\s+\$|\ba fee of\s+\$", re.IGNORECASE)

# Sub-clause patterns
PROVIDED_RE = re.compile(r"\bProvided(?:\s+further)?,?\s+That\b", re.IGNORECASE)
EARMARK = re.compile(r"of the amount.{0,50}under this heading.{0,100}specified in the table", re.IGNORECASE | re.DOTALL)
AVAILABILITY = re.compile(r"of the amount.{0,100}shall remain available until", re.IGNORECASE | re.DOTALL)
SUB_ALLOC = re.compile(r"^\s*,?\s*\$[\d,]+\s+shall\s+be\s+(?:for|available)", re.IGNORECASE)
CAP = re.compile(r"not (?:more than|to exceed)\s+\$[\d,]+", re.IGNORECASE)
OF_WHICH_AVAIL = re.compile(r"^\s*of which.{0,80}\bshall remain available\b", re.IGNORECASE | re.DOTALL)
OF_WHICH_ALLOC = re.compile(r"^\s*of which\b", re.IGNORECASE)

# Splitting / extraction helpers
OF_WHICH_RE = re.compile(r",?\s*\bof which\b", re.IGNORECASE)
IN_ADDITION_RE = re.compile(r";\s*and,?\s*in addition,", re.IGNORECASE)
CAP_AMOUNT_RE = re.compile(r"not (?:more than|to exceed)\s+\$[\d,]+(?:\.\d+)?", re.IGNORECASE)
DOLLAR = re.compile(r"\$([\d,]+(?:\.\d+)?)")

PRIMARY_LABELS = {"appropriation", "authorization", "transfer", "rescission", "fee"}


def classify_text(text):
    if not text:
        return None
    if RESTRICT.match(text):
        return "restriction"
    if RESTRICT_NOTWITHSTANDING.match(text):
        return "restriction"
    if TRANSFER.match(text):
        return "transfer"
    if APPROP.match(text):
        return "rescission" if RESCISSION.search(text) else "appropriation"
    if RESCISSION.search(text):
        return "rescission"
    if DIRECTIVE.match(text):
        return "directive"
    if REPROGRAM.match(text):
        return "cap"
    if DELAYED_APPROP.match(text):
        return "appropriation"
    if APPROP_ALT.search(text):
        return "rescission" if RESCISSION.search(text) else "appropriation"
    if AUTHORIZATION.search(text):
        return "authorization"
    if FEE.search(text):
        return "fee"
    if EARMARK.search(text):
        return "earmark"
    if AVAILABILITY.search(text):
        return "availability"
    if SUB_ALLOC.match(text):
        return "sub_allocation"
    if OF_WHICH_AVAIL.match(text):
        return "availability"
    if OF_WHICH_ALLOC.match(text):
        return "sub_allocation"
    if CAP.search(text):
        return "cap"
    return "unknown"


def split_clauses(text):
    """Split on Provided That → ; and in addition → of which, in that order."""
    if not text:
        return []
    results = []
    for i, provided_part in enumerate(PROVIDED_RE.split(text)):
        level = "primary" if i == 0 else "sub"
        for j, addition_part in enumerate(IN_ADDITION_RE.split(provided_part)):
            of_which_parts = OF_WHICH_RE.split(addition_part)
            for k, clause in enumerate(of_which_parts):
                sub_level = level if (j == 0 and k == 0) else "sub"
                prefix = "of which " if k > 0 else ""
                results.append((prefix + clause.strip(), sub_level))
    return results


def non_cap_amounts(text):
    """Dollar amounts not preceded by cap language."""
    return DOLLAR.findall(CAP_AMOUNT_RE.sub("", text))


def build_financial_df(tree):
    import pandas as pd

    rows = []
    for node_idx, n in enumerate(tree.nodes):
        if not DOLLAR.search(n.body_text or ""):
            continue
        account = " > ".join(n.display_path[-2:]) if n.display_path else ""
        node_label = classify_text(n.body_text)
        for clause_text, level in split_clauses(n.body_text):
            if not DOLLAR.search(clause_text):
                continue
            clean = non_cap_amounts(clause_text)
            m = DOLLAR.search(CAP_AMOUNT_RE.sub("", clause_text)) or DOLLAR.search(clause_text)
            amount = float(m.group(1).replace(",", "")) if m else None
            rows.append(
                {
                    "node_idx": node_idx,
                    "account": account,
                    "level": level,
                    "type": node_label if level == "primary" else classify_text(clause_text),
                    "amount": amount,
                    "needs_review": len(clean) > 1,
                    "preview": clause_text[:150],
                    "body_text": n.body_text or "",
                }
            )
    return pd.DataFrame(rows)


def group_financial_rows(df):
    """Group financial df rows into per-node lists, ordered by first appearance.

    Uses node_idx as the group boundary so that a node whose only dollar-bearing
    clause is labeled 'sub' still forms its own group. Grouping on level=='primary'
    would silently merge such a node into the preceding node's group.
    """
    return [[row for _, row in grp.iterrows()] for _, grp in df.groupby("node_idx", sort=False)]


def check_coverage(df, tree):
    """Return set of dropped node ordinals. Prints a one-line summary.

    Uses node ordinal (position in tree.nodes) as occurrence identity so that
    two nodes with identical body_text are tracked separately. A set-of-texts
    approach would silently pass if one of N identical-text occurrences was dropped.
    """
    dollar_idxs = {idx for idx, n in enumerate(tree.nodes) if DOLLAR.search(n.body_text or "")}
    df_idxs = set(df["node_idx"]) if not df.empty else set()
    dropped = dollar_idxs - df_idxs
    if dropped:
        print(f"⚠  {len(dropped)} of {len(dollar_idxs)} dollar-amount node occurrences missing from financial table")
    else:
        print(f"✓  All {len(dollar_idxs)} dollar-amount node occurrences represented ({len(df)} rows)")
    return dropped

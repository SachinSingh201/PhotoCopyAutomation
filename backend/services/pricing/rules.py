import json
from typing import Any, Dict, List, Literal, Tuple
from backend.core.exceptions import AppException, ErrorCode
from backend.schemas.llm import PageRule


def validate_and_normalize_file_rules(
    page_count: int,
    file_color_mode: Literal["bw", "color"],
    file_default_sides: Literal["single", "double"],
    custom_rules: List[PageRule],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Validates page rules against total page count and builds a complete per-page breakdown.
    Returns:
      (normalized_rules_json_data, page_breakdown_dict)
      where page_breakdown_dict has keys:
      'bw_single', 'bw_double', 'color_single', 'color_double'
    """
    if page_count <= 0:
        raise AppException(
            code=ErrorCode.ZERO_PAGE_FILE,
            message="Document has 0 pages.",
        )

    # Initialize per-page map (1 to page_count)
    # Default settings from file-level
    page_map: Dict[int, Dict[str, str]] = {}
    for p in range(1, page_count + 1):
        page_map[p] = {
            "sides": file_default_sides,
            "color": file_color_mode,
        }

    # Apply custom rules in order
    for rule in custom_rules:
        start = rule.page_start
        end = rule.page_end if rule.page_end is not None else page_count

        if start < 1:
            raise AppException(
                code=ErrorCode.INVALID_PAGE_RANGE,
                message=f"Rule page_start {start} must be >= 1",
            )
        if end > page_count:
            raise AppException(
                code=ErrorCode.INVALID_PAGE_RANGE,
                message=f"Rule page_end {end} exceeds document page count {page_count}",
            )
        if start > end:
            raise AppException(
                code=ErrorCode.INVALID_PAGE_RANGE,
                message=f"Rule page_start {start} cannot be greater than page_end {end}",
            )

        for p in range(start, end + 1):
            page_map[p]["sides"] = rule.sides
            if rule.color:
                page_map[p]["color"] = rule.color

    # Count pages per combination
    breakdown = {
        "bw_single": 0,
        "bw_double": 0,
        "color_single": 0,
        "color_double": 0,
    }

    for p in range(1, page_count + 1):
        sides = page_map[p]["sides"]
        color = page_map[p]["color"]
        key = f"{color}_{sides}"
        if key in breakdown:
            breakdown[key] += 1

    # Convert custom rules to serializable list
    normalized_rules = [r.model_dump() for r in custom_rules]
    return normalized_rules, breakdown

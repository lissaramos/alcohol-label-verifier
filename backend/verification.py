import difflib
import re
from dataclasses import dataclass

FIELD_BRAND_NAME = "brand_name"
FIELD_CLASS_TYPE = "class_type"
FIELD_ALCOHOL_CONTENT = "alcohol_content"
FIELD_NET_CONTENTS = "net_contents"
FIELD_GOVERNMENT_WARNING = "government_warning"
FIELD_SULFITE_DECLARATION = "sulfite_declaration"

BEVERAGE_DISTILLED_SPIRITS = "distilled_spirits"
BEVERAGE_WINE = "wine"
BEVERAGE_BEER = "beer"
BEVERAGE_TYPES = (BEVERAGE_DISTILLED_SPIRITS, BEVERAGE_WINE, BEVERAGE_BEER)

STATUS_MATCH = "match"
STATUS_REVIEW = "review"
STATUS_MISMATCH = "mismatch"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_APPLICABLE = "not_applicable"

# 27 CFR 16.21 — the exact wording every alcohol label is legally required to
# carry, regardless of beverage type. Jenny's note was explicit: this must
# match word-for-word, including case, with no exceptions. Because Tesseract
# preserves case as printed, a case-sensitive substring check on the OCR text
# directly enforces this without needing any font-weight/bold detection.
REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

_PERCENT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# "fl oz" (with or without periods/space) is the standard beer net-contents
# unit and must come before the bare "oz" alternative.
_VOLUME_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz\.?|ml|l|oz)\b", re.IGNORECASE)
_TABLE_OR_LIGHT_WINE_PATTERN = re.compile(r"\b(table|light)\s+wine\b", re.IGNORECASE)

_REVIEW_THRESHOLD = 0.85
_MISMATCH_FLOOR = 0.5

# Distilled spirits: TTB expects an exact, accurate ABV statement.
_SPIRITS_MATCH_TOLERANCE = 0.05
_SPIRITS_REVIEW_TOLERANCE = 0.5

# Wine: 27 CFR 4.36 gives a legal tolerance around the stated ABV — wider
# below 14%, tighter at/above it (fortified wines).
_WINE_TOLERANCE_UNDER_14 = 1.5
_WINE_TOLERANCE_14_AND_OVER = 1.0
_WINE_REVIEW_BUFFER = 0.5


@dataclass
class FieldResult:
    field_name: str
    submitted_value: str
    extracted_value: str | None
    status: str
    similarity: float | None = None


def _normalize_words(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_unit(unit: str) -> str:
    """Collapses unit spelling variants ("fl oz", "fl. oz.", "FLOZ") so they compare equal."""
    return re.sub(r"[.\s]", "", unit).lower()


def _word_ngram_windows(haystack_words: list[str], n: int) -> list[str]:
    if len(haystack_words) < n:
        return [" ".join(haystack_words)] if haystack_words else []
    return [" ".join(haystack_words[i : i + n]) for i in range(len(haystack_words) - n + 1)]


def _best_partial_match(needle: str, haystack: str) -> tuple[str | None, float]:
    haystack_words = haystack.split()
    n = max(1, len(needle.split()))
    windows = _word_ngram_windows(haystack_words, n)
    if not windows:
        return None, 0.0

    best_window = max(windows, key=lambda w: difflib.SequenceMatcher(None, needle, w).ratio())
    best_ratio = difflib.SequenceMatcher(None, needle, best_window).ratio()
    return best_window, best_ratio


def match_text_field(submitted: str, ocr_text: str) -> FieldResult:
    """Searches for `submitted` as a substring/near-match anywhere in `ocr_text`.

    Because this looks for the expected value rather than requiring the label
    to contain *only* the expected fields, it's naturally tolerant of extra
    text on the label — e.g. a personalized message alongside the mandatory
    fields — without any special-casing.
    """
    norm_submitted = _normalize_words(submitted)
    norm_ocr = _normalize_words(ocr_text)

    if not norm_submitted:
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)

    if norm_submitted in norm_ocr:
        return FieldResult("", submitted, submitted, STATUS_MATCH, 1.0)

    window, ratio = _best_partial_match(norm_submitted, norm_ocr)
    if ratio >= _REVIEW_THRESHOLD:
        return FieldResult("", submitted, window, STATUS_REVIEW, ratio)
    if ratio >= _MISMATCH_FLOOR:
        return FieldResult("", submitted, window, STATUS_MISMATCH, ratio)
    return FieldResult("", submitted, None, STATUS_NOT_FOUND, ratio)


def _match_percent_with_tolerance(
    submitted_value: float, ocr_text: str, match_tolerance: float, review_tolerance: float
) -> tuple[str | None, float, str]:
    ocr_matches = [float(m) for m in _PERCENT_PATTERN.findall(ocr_text)]
    if not ocr_matches:
        return None, 0.0, STATUS_NOT_FOUND

    closest = min(ocr_matches, key=lambda v: abs(v - submitted_value))
    diff = abs(closest - submitted_value)
    extracted = f"{closest}%"

    if diff <= match_tolerance:
        return extracted, diff, STATUS_MATCH
    if diff <= review_tolerance:
        return extracted, diff, STATUS_REVIEW
    return extracted, diff, STATUS_MISMATCH


def match_alcohol_content(submitted: str, ocr_text: str, beverage_type: str) -> FieldResult:
    submitted = submitted.strip()

    if beverage_type == BEVERAGE_BEER and not submitted:
        # TTB does not federally require an ABV statement on malt beverage
        # labels — if the agent left it blank, there's nothing to verify.
        return FieldResult("", submitted, None, STATUS_NOT_APPLICABLE)

    submitted_matches = _PERCENT_PATTERN.findall(submitted)
    if not submitted_matches:
        if beverage_type == BEVERAGE_BEER:
            return FieldResult("", submitted, None, STATUS_NOT_APPLICABLE)
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)
    submitted_value = float(submitted_matches[0])

    if beverage_type == BEVERAGE_WINE:
        tolerance = (
            _WINE_TOLERANCE_UNDER_14 if submitted_value < 14 else _WINE_TOLERANCE_14_AND_OVER
        )
        extracted, _, status = _match_percent_with_tolerance(
            submitted_value, ocr_text, tolerance, tolerance + _WINE_REVIEW_BUFFER
        )

        # Wines between 7% and 14% ABV may state "Table Wine" or "Light Wine"
        # in lieu of a percentage (27 CFR 4.36) — accept either.
        if status != STATUS_MATCH and 7 <= submitted_value <= 14:
            phrase_match = _TABLE_OR_LIGHT_WINE_PATTERN.search(ocr_text)
            if phrase_match:
                return FieldResult("", submitted, phrase_match.group(0), STATUS_MATCH)

        return FieldResult("", submitted, extracted, status)

    # Distilled spirits (default) and beer-with-a-stated-ABV both expect an
    # accurate, closely-matching statement.
    extracted, _, status = _match_percent_with_tolerance(
        submitted_value, ocr_text, _SPIRITS_MATCH_TOLERANCE, _SPIRITS_REVIEW_TOLERANCE
    )
    return FieldResult("", submitted, extracted, status)


def match_net_contents(submitted: str, ocr_text: str) -> FieldResult:
    submitted_match = _VOLUME_PATTERN.search(submitted)
    if not submitted_match:
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)
    submitted_value = float(submitted_match.group(1))
    submitted_unit = _normalize_unit(submitted_match.group(2))

    for value_str, unit in _VOLUME_PATTERN.findall(ocr_text):
        value = float(value_str)
        if _normalize_unit(unit) == submitted_unit and abs(value - submitted_value) < 0.01:
            return FieldResult("", submitted, f"{value_str} {unit.strip()}", STATUS_MATCH)

    ocr_matches = _VOLUME_PATTERN.findall(ocr_text)
    if ocr_matches:
        value_str, unit = ocr_matches[0]
        return FieldResult("", submitted, f"{value_str} {unit.strip()}", STATUS_MISMATCH)
    return FieldResult("", submitted, None, STATUS_NOT_FOUND)


def match_government_warning(ocr_text: str) -> FieldResult:
    collapsed_ocr = _collapse_whitespace(ocr_text)
    collapsed_required = _collapse_whitespace(REQUIRED_WARNING)

    if collapsed_required in collapsed_ocr:
        return FieldResult("", REQUIRED_WARNING, REQUIRED_WARNING, STATUS_MATCH)

    lower_idx = collapsed_ocr.lower().find("government warning")
    if lower_idx != -1:
        snippet = collapsed_ocr[lower_idx : lower_idx + len(collapsed_required) + 40]
        return FieldResult("", REQUIRED_WARNING, snippet, STATUS_MISMATCH)

    return FieldResult("", REQUIRED_WARNING, None, STATUS_NOT_FOUND)


def match_sulfite_declaration(ocr_text: str) -> FieldResult:
    """Wine containing >= 10ppm sulfur dioxide must carry a "Contains
    Sulfites" statement (27 CFR 4.32(e)). We can't tell from the label alone
    whether the product actually contains that much — so a missing statement
    is surfaced for agent review rather than treated as an automatic failure.
    """
    required = "Contains Sulfites"
    collapsed_ocr = _collapse_whitespace(ocr_text)
    lower_ocr = collapsed_ocr.lower()

    idx = lower_ocr.find("sulfite")
    if idx == -1:
        return FieldResult("", required, None, STATUS_NOT_FOUND)

    snippet_start = max(0, idx - 15)
    snippet = collapsed_ocr[snippet_start : idx + 20].strip()
    return FieldResult("", required, snippet, STATUS_MATCH)


def run_verification(
    application_fields: dict[str, str], ocr_text: str, beverage_type: str
) -> list[FieldResult]:
    results = [
        match_text_field(application_fields[FIELD_BRAND_NAME], ocr_text),
        match_text_field(application_fields[FIELD_CLASS_TYPE], ocr_text),
        match_alcohol_content(application_fields[FIELD_ALCOHOL_CONTENT], ocr_text, beverage_type),
        match_net_contents(application_fields[FIELD_NET_CONTENTS], ocr_text),
        match_government_warning(ocr_text),
    ]
    field_names = [
        FIELD_BRAND_NAME,
        FIELD_CLASS_TYPE,
        FIELD_ALCOHOL_CONTENT,
        FIELD_NET_CONTENTS,
        FIELD_GOVERNMENT_WARNING,
    ]

    if beverage_type == BEVERAGE_WINE:
        results.append(match_sulfite_declaration(ocr_text))
        field_names.append(FIELD_SULFITE_DECLARATION)

    for result, name in zip(results, field_names):
        result.field_name = name

    return results


def compute_overall_status(results: list[FieldResult]) -> str:
    warning_result = next(r for r in results if r.field_name == FIELD_GOVERNMENT_WARNING)
    if warning_result.status != STATUS_MATCH:
        return "fail"
    if any(r.status == STATUS_MISMATCH for r in results):
        return "fail"
    if any(r.status in (STATUS_REVIEW, STATUS_NOT_FOUND) for r in results):
        return "needs_review"
    return "pass"

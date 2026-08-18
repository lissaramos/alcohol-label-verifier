import difflib
import re
from dataclasses import dataclass

FIELD_BRAND_NAME = "brand_name"
FIELD_CLASS_TYPE = "class_type"
FIELD_ALCOHOL_CONTENT = "alcohol_content"
FIELD_NET_CONTENTS = "net_contents"
FIELD_GOVERNMENT_WARNING = "government_warning"

STATUS_MATCH = "match"
STATUS_REVIEW = "review"
STATUS_MISMATCH = "mismatch"
STATUS_NOT_FOUND = "not_found"

# 27 CFR 16.21 — the exact wording every alcohol label is legally required to
# carry. Jenny's note was explicit: this must match word-for-word, including
# case, with no exceptions. Because Tesseract preserves case as printed, a
# case-sensitive substring check on the OCR text directly enforces this
# without needing any font-weight/bold detection.
REQUIRED_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

_PERCENT_PATTERN = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_VOLUME_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|oz)\b", re.IGNORECASE)

_REVIEW_THRESHOLD = 0.85
_MISMATCH_FLOOR = 0.5

_ABV_MATCH_TOLERANCE = 0.05
_ABV_REVIEW_TOLERANCE = 0.5


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


def match_alcohol_content(submitted: str, ocr_text: str) -> FieldResult:
    submitted_matches = _PERCENT_PATTERN.findall(submitted)
    if not submitted_matches:
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)
    submitted_value = float(submitted_matches[0])

    ocr_matches = [float(m) for m in _PERCENT_PATTERN.findall(ocr_text)]
    if not ocr_matches:
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)

    closest = min(ocr_matches, key=lambda v: abs(v - submitted_value))
    diff = abs(closest - submitted_value)
    extracted = f"{closest}%"

    if diff <= _ABV_MATCH_TOLERANCE:
        return FieldResult("", submitted, extracted, STATUS_MATCH)
    if diff <= _ABV_REVIEW_TOLERANCE:
        return FieldResult("", submitted, extracted, STATUS_REVIEW)
    return FieldResult("", submitted, extracted, STATUS_MISMATCH)


def match_net_contents(submitted: str, ocr_text: str) -> FieldResult:
    submitted_match = _VOLUME_PATTERN.search(submitted)
    if not submitted_match:
        return FieldResult("", submitted, None, STATUS_NOT_FOUND)
    submitted_value = float(submitted_match.group(1))
    submitted_unit = submitted_match.group(2).lower()

    for value_str, unit in _VOLUME_PATTERN.findall(ocr_text):
        value = float(value_str)
        unit = unit.lower()
        if unit == submitted_unit and abs(value - submitted_value) < 0.01:
            return FieldResult("", submitted, f"{value} {unit}", STATUS_MATCH)

    ocr_matches = _VOLUME_PATTERN.findall(ocr_text)
    if ocr_matches:
        value_str, unit = ocr_matches[0]
        return FieldResult("", submitted, f"{value_str} {unit.lower()}", STATUS_MISMATCH)
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


def run_verification(application_fields: dict[str, str], ocr_text: str) -> list[FieldResult]:
    results = [
        match_text_field(application_fields[FIELD_BRAND_NAME], ocr_text),
        match_text_field(application_fields[FIELD_CLASS_TYPE], ocr_text),
        match_alcohol_content(application_fields[FIELD_ALCOHOL_CONTENT], ocr_text),
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

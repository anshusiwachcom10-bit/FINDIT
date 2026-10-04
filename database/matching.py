import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher


MATCH_WEIGHTS = {
    "category": 25,
    "name": 20,
    "description": 20,
    "location": 15,
    "date": 10,
    "attributes": 10,
}
MIN_MATCH_SCORE = 50
MATCH_STATUSES = ("potential", "reviewed", "contacted", "resolved", "rejected")

_STOP_WORDS = {
    "a", "an", "and", "are", "at", "by", "for", "from", "found", "has",
    "have", "i", "in", "is", "it", "its", "lost", "my", "of", "on", "or",
    "the", "this", "to", "was", "with",
}
_SYNONYM_GROUPS = (
    {"wallet", "purse"},
    {"slot", "compartment", "pocket"},
    {"cellphone", "mobile", "smartphone"},
    {"handbag", "purse", "pocketbook"},
    {"backpack", "rucksack", "knapsack"},
    {"sneaker", "trainer", "tennis shoe"},
    {"eyeglasses", "glasses", "spectacles"},
    {"id", "identification", "identity card"},
    {"dark", "deep"},
)
_SYNONYMS = {
    word: min(group, key=len)
    for group in _SYNONYM_GROUPS
    for word in group
}
_ATTRIBUTE_GROUPS = {
    "color": {
        "black", "white", "gray", "grey", "silver", "brown", "tan",
        "beige", "cream", "red", "maroon", "orange", "yellow", "green",
        "olive", "blue", "navy", "purple", "pink", "gold",
    },
    "material": {
        "leather", "suede", "canvas", "cotton", "wool", "denim", "nylon",
        "plastic", "metal", "steel", "stainless", "wood", "wooden", "rubber",
        "silicone", "glass", "fabric",
    },
    "feature": {
        "zipper", "zip", "buckle", "clasp", "strap", "stitched", "stitching",
        "engraved", "engraving", "monogram", "initial", "initials", "logo",
        "pattern", "striped", "floral", "cracked", "scratched", "broken",
        "brass", "round", "square", "folding", "foldable", "three", "two",
        "one",
    },
    "brand": {
        "adidas", "apple", "coach", "dell", "fossil", "gucci", "lenovo",
        "michael", "microsoft", "nike", "samsung", "sony", "tumi",
    },
}


def _normalise(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _tokens(value, remove_stop_words=True):
    result = []
    for token in _normalise(value).split():
        if remove_stop_words and token in _STOP_WORDS:
            continue
        if len(token) > 3 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        result.append(_SYNONYMS.get(token, token))
    return result


def _token_similarity(left, right):
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    unmatched = list(right_tokens)
    matches = 0
    for left_token in left_tokens:
        best_index = -1
        best_score = 0.0
        for index, right_token in enumerate(unmatched):
            similarity = SequenceMatcher(None, left_token, right_token).ratio()
            if left_token == right_token:
                similarity = 1.0
            elif min(len(left_token), len(right_token)) >= 4 and (
                left_token.startswith(right_token) or right_token.startswith(left_token)
            ):
                similarity = max(similarity, 0.88)
            if similarity > best_score:
                best_score = similarity
                best_index = index
        if best_index >= 0 and best_score >= 0.84:
            matches += best_score
            unmatched.pop(best_index)

    precision = matches / len(left_tokens)
    recall = matches / len(right_tokens)
    token_f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    phrase_similarity = SequenceMatcher(
        None,
        " ".join(left_tokens),
        " ".join(right_tokens),
    ).ratio()
    return max(0.0, min(1.0, 0.8 * token_f1 + 0.2 * phrase_similarity))


def _category_similarity(left, right):
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 1.0
    return _token_similarity(left, right) if set(left_tokens) & set(right_tokens) else 0.0


def _date_similarity(left, right):
    try:
        days = abs((date.fromisoformat(str(left)) - date.fromisoformat(str(right))).days)
    except (TypeError, ValueError):
        return 0.0, None
    return max(0.0, 1.0 - days / 30.0), days


def _attributes(item):
    source = " ".join((item.get("title") or "", item.get("description") or ""))
    found = {}
    for group, vocabulary in _ATTRIBUTE_GROUPS.items():
        values = set(_tokens(source, remove_stop_words=False)) & vocabulary
        if values:
            found[group] = values
    return found


def _attribute_similarity(left, right):
    left_attributes = _attributes(left)
    right_attributes = _attributes(right)
    shared = []
    compared = []
    for group in _ATTRIBUTE_GROUPS:
        left_values = left_attributes.get(group, set())
        right_values = right_attributes.get(group, set())
        if not left_values or not right_values:
            continue
        compared.append(
            len(left_values & right_values) / len(left_values | right_values)
        )
        shared.extend((group, value) for value in sorted(left_values & right_values))
    return (sum(compared) / len(compared) if compared else 0.0), shared


def _match_level(score):
    if score >= 85:
        return "Strong Match"
    if score >= 70:
        return "Likely Match"
    if score >= 50:
        return "Possible Match"
    return "Low Match"


def score_pair(left, right):
    """Score two reports and return an explainable, deterministic breakdown."""
    if left.get("type") == right.get("type"):
        return None
    if left.get("type") not in ("lost", "found") or right.get("type") not in ("lost", "found"):
        return None
    if left.get("user_id") and left.get("user_id") == right.get("user_id"):
        return None
    if (left.get("status") or "open").lower() != "open" or (
        right.get("status") or "open"
    ).lower() != "open":
        return None

    category = _category_similarity(left.get("category"), right.get("category"))
    name = _token_similarity(left.get("title"), right.get("title"))
    description = _token_similarity(left.get("description"), right.get("description"))
    location = _token_similarity(left.get("location"), right.get("location"))
    date_score, date_days = _date_similarity(left.get("event_date"), right.get("event_date"))
    attributes, shared_attributes = _attribute_similarity(left, right)

    component_scores = {
        "category": round(category * 100),
        "name": round(name * 100),
        "description": round(description * 100),
        "location": round(location * 100),
        "date": round(date_score * 100),
        "attributes": round(attributes * 100),
    }
    score = round(
        sum(
            MATCH_WEIGHTS[field] * value
            for field, value in (
                ("category", category),
                ("name", name),
                ("description", description),
                ("location", location),
                ("date", date_score),
                ("attributes", attributes),
            )
        )
    )

    categories_differ = (
        bool(_normalise(left.get("category")))
        and bool(_normalise(right.get("category")))
        and category == 0
    )
    if categories_differ:
        score = min(score, MIN_MATCH_SCORE - 1)

    reasons = []
    if category >= 0.9:
        reasons.append("Same category")
    elif category >= 0.5:
        reasons.append("Similar category")
    if name >= 0.45:
        reasons.append("Similar item name")
    if description >= 0.35:
        reasons.append("Similar description")
    if location >= 0.55:
        if _normalise(left.get("location")) == _normalise(right.get("location")):
            reasons.append("Same reported location")
        else:
            reasons.append("Similar reported location")
    if date_days is not None and date_days <= 30:
        reasons.append(f"Report dates {date_days} {'day' if date_days == 1 else 'days'} apart")
    for group, value in shared_attributes:
        label = {"color": "color", "material": "material", "brand": "brand"}.get(group, "detail")
        reason = f"Similar {label}: {value}"
        if reason not in reasons:
            reasons.append(reason)

    return {
        "score": score,
        "confidence": score,
        "level": _match_level(score),
        "reasons": reasons,
        "components": component_scores,
        "weights": dict(MATCH_WEIGHTS),
        "date_difference_days": date_days,
    }


def find_matches(item, candidates, minimum_score=MIN_MATCH_SCORE):
    results = []
    for candidate in candidates:
        if candidate.get("id") == item.get("id"):
            continue
        result = score_pair(item, candidate)
        if result is None or result["score"] < minimum_score:
            continue
        results.append({"item": dict(candidate), **result})
    return sorted(
        results,
        key=lambda result: (
            -result["score"],
            result["item"].get("created_at") or "",
            result["item"].get("id") or 0,
        ),
    )

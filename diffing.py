import difflib


def compute_diff(old_text: str, new_text: str) -> str:
    old_words = old_text.split()
    new_words = new_text.split()
    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "delete":
            parts.append(f"[-{' '.join(old_words[i1:i2])}-]")
        elif tag == "insert":
            parts.append(f"{{+{' '.join(new_words[j1:j2])}+}}")
        elif tag == "replace":
            parts.append(f"[-{' '.join(old_words[i1:i2])}-]{{+{' '.join(new_words[j1:j2])}+}}")
    return " ".join(parts)

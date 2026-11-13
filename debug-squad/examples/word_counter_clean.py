def count_words(text: str) -> int:
    """Return the number of whitespace-separated words in text.

    An empty or whitespace-only string contains no words, so 0 is returned.
    """
    return len(text.split())

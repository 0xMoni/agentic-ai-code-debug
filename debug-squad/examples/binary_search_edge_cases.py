def binary_search(items, target):
    """Return the index of target in a sorted list, or -1 if absent."""
    low = 0
    high = len(items)

    while low <= high:
        mid = (low + high) / 2
        if items[mid] == target:
            return mid
        elif items[mid] < target:
            low = mid
        else:
            high = mid

    return -1

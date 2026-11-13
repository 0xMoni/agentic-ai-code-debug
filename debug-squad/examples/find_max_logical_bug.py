def find_max(nums):
    maximum = 0
    for num in nums:
        if num > maximum:
            maximum = num
    return maximum

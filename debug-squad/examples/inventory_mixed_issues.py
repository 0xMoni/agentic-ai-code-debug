import os
import json


def process(d, discount_percent):
    total = 0
    for k in d:
        total = total + d[k]["price"] * d[k]["qty"]

    if discount_percent > 0:
        total = total - (total * discount_percent / 100)

    return total


def most_expensive(d):
    best = None
    for k in d:
        if best == None or d[k]["price"] > d[best]["price"]:
            best = k
    return d[best]["price"]

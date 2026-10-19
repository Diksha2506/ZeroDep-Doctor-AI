"""Module A forming a circular dependency with Module B."""

from module_b import process_data_b

def process_data_a():
    return process_data_b() + 1

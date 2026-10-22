"""Module B forming a circular dependency with Module A."""

from module_a import process_data_a

def process_data_b():
    return 42

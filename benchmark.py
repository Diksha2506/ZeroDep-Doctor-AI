"""Benchmark suite measuring execution time across 10, 100, and 1,000 synthetic Python files."""

import time
import tempfile
from pathlib import Path
from zerodep.cli import scan_project

def generate_benchmark_repo(target_dir: Path, num_files: int):
    req_file = target_dir / "requirements.txt"
    req_file.write_text("requests==2.28.0\nclick>=8.0\npytz\ncolorama\n")
    
    for i in range(num_files):
        py_file = target_dir / f"module_{i}.py"
        py_file.write_text(f"""
import requests as req
import click
# Module {i} logic
def run_{i}():
    return req.get('https://example.com/{i}').json()
""")

def run_benchmarks():
    print("================================================================================")
    print("                   ZERODEP DOCTOR - PERFORMANCE BENCHMARK                       ")
    print("================================================================================")
    
    for count in (10, 100, 1000):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            generate_benchmark_repo(tmp_path, count)
            
            start = time.time()
            scan = scan_project(tmp_path)
            duration = time.time() - start
            
            print(f" Repository Size: {count:<4} files | Scanned: {scan['files_scanned']:<4} | LOC: {scan['loc']:<6} | Time: {duration:.4f}s")
    print("================================================================================")

if __name__ == "__main__":
    run_benchmarks()

"""Tests for arbitrary path handling, manifest formats, and edge cases."""
import pytest
from pathlib import Path
from zerodep.cli import scan_project
from zerodep.manifest_parser import parse_manifests, ManifestDependency
from zerodep.scoring_engine import calculate_score


def test_path_with_spaces(tmp_path):
    """Test project in directory path containing spaces."""
    project_dir = tmp_path / "My Folder With Spaces" / "Game Project"
    project_dir.mkdir(parents=True)
    
    (project_dir / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    (project_dir / "main.py").write_text(
        "import requests\nresp = requests.get('https://example.com')\n",
        encoding="utf-8"
    )
    
    result = scan_project(project_dir)
    assert result["files_scanned"] == 1
    assert "requests" in result["declared_deps"]
    assert "requests" in result["used_deps"]
    assert result["score_info"].audit_status == "ASSESSED"


def test_relative_path_resolution(tmp_path, monkeypatch):
    """Test relative path resolves correctly relative to current working directory."""
    monkeypatch.chdir(tmp_path)
    rel_dir = Path("sub_project")
    rel_dir.mkdir()
    (rel_dir / "app.py").write_text("import sys\nprint(sys.version)\n", encoding="utf-8")
    
    result = scan_project(rel_dir)
    assert result["files_scanned"] == 1
    assert result["score_info"].audit_status == "GENUINE_ZERO_DEP"
    assert result["score_info"].current_score == 100.0


def test_empty_directory_handling(tmp_path):
    """Empty directory yields UNABLE_TO_ASSESS with score 0.0 and grade N/A."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    
    result = scan_project(empty_dir)
    assert result["files_scanned"] == 0
    assert result["score_info"].audit_status == "UNABLE_TO_ASSESS"
    assert result["score_info"].current_score == 0.0
    assert result["score_info"].grade == "N/A"


def test_nested_python_files(tmp_path):
    """Verify recursive scanning finds python files in subdirectories."""
    nested = tmp_path / "src" / "core" / "utils"
    nested.mkdir(parents=True)
    (nested / "math_helper.py").write_text("import math\ndef add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "entrypoint.py").write_text("from src.core.utils.math_helper import add\n", encoding="utf-8")
    
    result = scan_project(tmp_path)
    assert result["files_scanned"] == 2
    assert result["score_info"].audit_status == "GENUINE_ZERO_DEP"


def test_pyproject_toml_only(tmp_path):
    """Test project with pyproject.toml and no requirements.txt."""
    pyproject_content = """[project]
name = "cool-pkg"
version = "0.1.0"
dependencies = [
    "requests>=2.0",
    "scikit-learn"
]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
    (tmp_path / "worker.py").write_text("import sklearn\nfrom sklearn.linear_model import LogisticRegression\n", encoding="utf-8")
    
    result = scan_project(tmp_path)
    assert result["files_scanned"] == 1
    assert "scikit-learn" in result["declared_deps"]
    assert "requests" in result["declared_deps"]
    assert "scikit-learn" in result["used_deps"]
    assert "requests" not in result["used_deps"]  # Unused


def test_setup_cfg_manifest_parsing(tmp_path):
    """Test setup.cfg install_requires parsing."""
    setup_cfg = """[metadata]
name = example-tool
version = 1.0.0

[options]
install_requires =
    click>=8.0
    colorama
    scikit-learn
"""
    (tmp_path / "setup.cfg").write_text(setup_cfg, encoding="utf-8")
    mr = parse_manifests(tmp_path)
    dep_names = {d.name for d in mr.dependencies}
    assert dep_names == {"click", "colorama", "scikit-learn"}
    # Verify scikit-learn alias to sklearn
    sklearn_dep = next(d for d in mr.dependencies if d.name == "scikit-learn")
    assert sklearn_dep.import_name == "sklearn"


def test_pipfile_manifest_parsing(tmp_path):
    """Test Pipfile [packages] parsing."""
    pipfile = """[[source]]
url = "https://pypi.org/simple"
verify_ssl = true
name = "pypi"

[packages]
requests = "*"
fastapi = ">=0.95"
opencv-python = "~=4.7"

[dev-packages]
pytest = "*"
"""
    (tmp_path / "Pipfile").write_text(pipfile, encoding="utf-8")
    mr = parse_manifests(tmp_path)
    dep_names = {d.name for d in mr.dependencies}
    assert "requests" in dep_names
    assert "fastapi" in dep_names
    assert "opencv-python" in dep_names
    assert "pytest" in dep_names
    # Check scopes
    req_dep = next(d for d in mr.dependencies if d.name == "requests")
    assert req_dep.scope == "runtime"
    pytest_dep = next(d for d in mr.dependencies if d.name == "pytest")
    assert pytest_dep.scope == "dev"
    cv_dep = next(d for d in mr.dependencies if d.name == "opencv-python")
    assert cv_dep.import_name == "cv2"


def test_bom_prefixed_files(tmp_path):
    """Ensure UTF-8 BOM does not cause SyntaxError or abort scanning."""
    bom_file = tmp_path / "bom_module.py"
    # Write UTF-8 BOM + python code
    bom_content = b"\xef\xbb\xbfimport math\nprint(math.pi)\n"
    bom_file.write_bytes(bom_content)
    
    result = scan_project(tmp_path)
    assert result["files_scanned"] == 1
    assert len(result["parse_errors"]) == 0
    assert result["score_info"].audit_status == "GENUINE_ZERO_DEP"


def test_partial_syntax_error_isolation(tmp_path):
    """A syntax error in one file should not prevent scanning valid files."""
    valid_file = tmp_path / "good.py"
    valid_file.write_text("import json\nprint(json.dumps({}))\n", encoding="utf-8")
    
    broken_file = tmp_path / "bad.py"
    broken_file.write_text("def broken_syntax(:\n", encoding="utf-8")
    
    result = scan_project(tmp_path)
    assert result["files_scanned"] == 2
    assert len(result["parse_errors"]) == 1
    assert "bad.py" in result["parse_errors"][0]
    # The valid file was scanned and LOC counted
    assert result["loc"] > 1


def test_undeclared_third_party_detection(tmp_path):
    """Importing third-party package without manifest triggers UNDECLARED_DEPS_FOUND."""
    py_file = tmp_path / "game.py"
    py_file.write_text("from PIL import Image\nimg = Image.open('hero.png')\n", encoding="utf-8")
    
    result = scan_project(tmp_path)
    assert result["files_scanned"] == 1
    assert "pil" in result["undeclared_third_party"]
    assert result["score_info"].audit_status == "UNDECLARED_DEPS_FOUND"
    assert result["score_info"].current_score == 90.0

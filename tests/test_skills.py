from edgedash.skills import canonical


ALIASES = {
    "k8s": "kubernetes",
    "postgresql": "postgres",
}


def test_case():
    assert canonical("PYTHON", ALIASES) == "python"


def test_whitespace():
    assert canonical("   python   ", ALIASES) == "python"


def test_parentheses():
    assert canonical("Kubernetes (EKS)", ALIASES) == "kubernetes"


def test_alias():
    assert canonical("K8S", ALIASES) == "kubernetes"


def test_no_alias():
    assert canonical("Docker", ALIASES) == "docker"


def test_empty_string():
    assert canonical("", ALIASES) == ""

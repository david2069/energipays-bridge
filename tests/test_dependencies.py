"""Every third-party module the bridge imports is installed by the image.

The MQTT integration imported aiomqtt lazily without declaring it, so every MQTT
integration failed in the add-on with "No module named 'aiomqtt'".
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# import name → distribution name, where they differ
DIST = {"Crypto": "pycryptodome", "paho": "paho-mqtt", "pydantic_settings": "pydantic-settings",
        # py-modules of energipays-client, installed from git by the Dockerfile
        "energipays": "energipays-client", "extract_key": "energipays-client"}


def _imports():
    names = set()
    for f in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n != "energipays_bridge"}


def _installed():
    text = (ROOT / "pyproject.toml").read_text()
    deps = re.search(r"^dependencies = \[(.*?)^\]", text, re.M | re.S).group(1)
    names = {re.split(r"[\[<>=!~ ]", d.strip())[0].lower() for d in re.findall(r'"([^"]+)"', deps)}
    if "energipays-client.git" in (ROOT / "Dockerfile").read_text():
        names.add("energipays-client")
    return names


def test_every_third_party_import_is_installed():
    installed = _installed()
    missing = sorted(n for n in _imports() if DIST.get(n, n).lower() not in installed)
    assert not missing, f"imported but not installed by pyproject/Dockerfile: {missing}"

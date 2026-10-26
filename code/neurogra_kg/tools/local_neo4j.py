"""Launch an installed Neo4j with all writable directories inside a workspace.

Does not install a Windows service or alter the installation's original database.
Credentials and PID are stored in ignored runtime output, never printed.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from ..utils import atomic_json


def prepare(root: Path, installation: Path, java: Path, port=7687):
    root = root.resolve()
    runtime = root / "output" / "kg" / "neo4j"
    if root not in runtime.resolve().parents:
        raise ValueError("neo4j_runtime_must_be_inside_workspace")
    for directory in ["conf", "data", "logs", "run", "import", "plugins"]:
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    if not (installation / "lib").is_dir() or not java.is_file():
        raise ValueError("installed_neo4j_and_java_required")
    credentials = runtime / "local_credentials.json"
    if not credentials.is_file():
        atomic_json(credentials, {"username": "neo4j", "password": secrets.token_urlsafe(24)})
    config = {
        "server.directories.data": runtime / "data", "server.directories.logs": runtime / "logs",
        "server.directories.run": runtime / "run", "server.directories.import": runtime / "import",
        "server.directories.plugins": runtime / "plugins", "server.directories.lib": installation / "lib",
        "server.default_listen_address": "127.0.0.1", "server.bolt.enabled": "true",
        "server.bolt.listen_address": f"127.0.0.1:{port}", "server.http.enabled": "false",
        "server.https.enabled": "false", "server.memory.heap.initial_size": "256m",
        "server.memory.heap.max_size": "512m", "server.memory.pagecache.size": "256m",
        "dbms.security.auth_enabled": "true",
    }
    (runtime / "conf" / "neo4j.conf").write_text("\n".join(f"{key}={str(value).replace(chr(92), '/')}"
        for key, value in config.items()) + "\n", encoding="utf-8")
    for name in ["user-logs.xml", "server-logs.xml"]:
        original = installation / "conf" / name
        if original.is_file():
            shutil.copyfile(original, runtime / "conf" / name)
    env = dict(os.environ)
    env["NEO4J_HOME"] = str(installation)
    env["NEO4J_CONF"] = str(runtime / "conf")
    env["JAVA_HOME"] = str(java.parent.parent)
    command = [str(java), "-cp", str(installation / "lib" / "*"), "org.neo4j.server.startup.Neo4jCommand", "console"]
    return runtime, env, command


def start(root, installation, java, port=7687):
    runtime, env, command = prepare(root, installation, java, port)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            raise ValueError("port_in_use_do_not_replace_existing_service")
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        if isinstance(exc, ValueError):
            raise
    credentials = json.loads((runtime / "local_credentials.json").read_text(encoding="utf-8"))
    if not (runtime / "data" / "dbms" / "auth.ini").is_file() and not (runtime / "data" / "databases" / "system").is_dir():
        admin = [str(java), "-cp", str(installation / "lib" / "*"),
                 "org.neo4j.server.startup.Neo4jAdminCommand", "dbms", "set-initial-password",
                 credentials["password"], "--require-password-change=false"]
        result = subprocess.run(admin, env=env, capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").replace(credentials["password"], "[redacted]")
            (runtime / "admin-error.log").write_text(error, encoding="utf-8")
            raise RuntimeError("neo4j_initial_password_failed:see_runtime_admin_log")
    with (runtime / "console.log").open("ab") as output:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
    atomic_json(runtime / "process.json", {"pid": process.pid, "port": port, "installation": str(installation),
                                          "java": str(java), "started_at": time.time()})
    return {"pid": process.pid, "port": port, "runtime": str(runtime)}


def credentials(config):
    path = config.resolve(config.paths.output_root) / "neo4j" / "local_credentials.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    os.environ[config.graph.password_env] = value["password"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7687)
    args = parser.parse_args()
    print(json.dumps(start(args.root.resolve(), args.installation.resolve(), args.java.resolve(), args.port)))


if __name__ == "__main__":
    main()

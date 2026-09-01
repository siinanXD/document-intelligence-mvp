"""Railway deploy artefacts: pins, region, no secrets, smoke script safety."""

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
RAILWAY_REGION = "europe-west4-drams3a"


def test_qdrant_deploy_image_matches_compose():
    compose = (ROOT / "docker-compose.yml").read_text()
    dockerfile = (DEPLOY / "Dockerfile.qdrant").read_text()
    assert "qdrant/qdrant:v1.12.1" in compose
    assert "qdrant/qdrant:v1.12.1" in dockerfile


def test_railway_configs_target_eu_west_and_private_qdrant_volume():
    for name in ("api", "worker", "web", "qdrant"):
        payload = json.loads((DEPLOY / "railway" / f"{name}.json").read_text())
        assert payload["deploy"]["region"] == RAILWAY_REGION
        assert payload["build"]["builder"] == "DOCKERFILE"
        assert payload["build"]["dockerfilePath"] == f"deploy/Dockerfile.{name}"
    qdrant = json.loads((DEPLOY / "railway" / "qdrant.json").read_text())
    assert qdrant["deploy"]["requiredMountPath"] == "/qdrant/storage"


def test_deploy_files_do_not_embed_secrets():
    for path in DEPLOY.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "sk-" not in text
        assert "begin " not in lowered or "private key" not in lowered
        assert "railway_api_token" not in lowered
        assert "openai_api_key=" not in lowered


def test_production_smoke_requires_base_url():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "production_smoke.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "SMOKE_BASE_URL" in result.stderr


def test_production_smoke_reports_health_without_payload_dump():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                body = b'{"status":"ok","version":"0.1.0","environment":"production"}'
            elif self.path == "/health/ready":
                body = (
                    b'{"status":"ready","version":"0.1.0","environment":"production",'
                    b'"checks":{"database":"up","vector_store":"up"}}'
                )
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "production_smoke.sh")],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "SMOKE_BASE_URL": f"http://127.0.0.1:{port}"},
        )
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    assert "smoke passed" in result.stdout
    assert "secret" not in result.stdout.lower()
    assert "filename" not in result.stdout.lower()

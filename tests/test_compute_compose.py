"""Offline contract tests: render Compose and exercise guards without Docker services."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.compute.yml"
BASE = "/mnt/tower/appdata-compute/commander-lab"
TEST_URL = "postgresql://commander_lab:placeholder@db:5432/commander_lab"


def render(database_url=TEST_URL, include_workers=True, **overrides):
    if not shutil.which("docker"):
        pytest.skip("docker CLI is required to validate Compose")
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    env.update(overrides)
    args = ["docker", "compose", "--env-file", "/dev/null", "-f", str(COMPOSE)]
    if include_workers:
        args.extend(["--profile", "workers", "--profile", "web"])
    return subprocess.run(
        args + ["config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
def config():
    result = render()
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def mounts(service):
    return {item["target"]: item for item in service["volumes"]}


def test_production_data_mounts_and_db_guard(config):
    services = config["services"]
    assert config["volumes"]["pgdata"] == {"external": True, "name": "commander-lab_pgdata"}
    db_mounts = mounts(services["db"])
    assert db_mounts["/var/lib/postgresql/data"]["source"] == "pgdata"
    assert db_mounts["/var/lib/postgresql/data"]["type"] == "volume"
    password = db_mounts["/run/secrets/postgres_password"]
    assert password["source"] == f"{BASE}/db-secrets/postgres_password"
    assert password["read_only"] is True
    assert password["bind"]["create_host_path"] is False
    assert services["db"]["environment"]["POSTGRES_PASSWORD_FILE"] == password["target"]
    assert "POSTGRES_PASSWORD" not in services["db"]["environment"]
    entrypoint = " ".join(services["db"]["entrypoint"])
    assert "PG_VERSION" in entrypoint and "!= 17" in entrypoint
    assert "exec docker-entrypoint.sh postgres" in entrypoint
    assert mounts(services["redis"])["/data"]["source"] == f"{BASE}/redis"
    redis_entrypoint = " ".join(services["redis"]["entrypoint"])
    assert "! -s /data/dump.rdb" in redis_entrypoint
    assert "exec docker-entrypoint.sh redis-server" in redis_entrypoint
    app_mounts = mounts(services["app"])
    for target, name, readonly in [("/data", "data", False),
                                    ("/app/.auth", "auth", True),
                                    ("/run/commander-lab-secrets", "secrets", True)]:
        mount = app_mounts[target]
        assert mount["source"] == f"{BASE}/{name}"
        assert mount.get("read_only", False) is readonly
    for service in services.values():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                assert mount["bind"]["create_host_path"] is False


def test_images_ports_and_health_order(config):
    services = config["services"]
    assert all("build" not in service for service in services.values())
    for name in ("app", "bracket-worker"):
        assert services[name]["image"] == "ghcr.io/andrewgari/commander-lab@sha256:b9a19d4ff35736dc013d60372acba06205d17abda28948d343cbdfc2c73b848b"
    assert services["app"]["environment"]["INTERACTIONS_DISABLED"] == "1"
    assert services["app"]["environment"]["DATA_ACCESS_MODE"] == "readonly"
    assert services["bracket-worker"]["profiles"] == ["workers"]
    assert services["web"]["profiles"] == ["web"]
    assert services["web"]["environment"]["PROTOCOL_HEADER"] == "x-forwarded-proto"
    assert services["web"]["environment"]["HOST_HEADER"] == "x-forwarded-host"
    for name, image in [("db", "commander-lab-db:pg17-vector0.8.2"),
                        ("web", "commander-lab-web:e8e69cd")]:
        assert services[name]["image"] == image
        assert services[name]["pull_policy"] == "never"
    assert services["redis"]["image"] == "redis:alpine"
    assert [(p["published"], p["target"]) for p in services["app"]["ports"]] == [("8020", 8000)]
    assert [(p["published"], p["target"]) for p in services["web"]["ports"]] == [("8011", 3000)]
    assert not services["db"].get("ports") and not services["redis"].get("ports")
    app_command = " ".join(services["app"]["command"])
    assert "--reload" not in app_command
    assert "a013_deck_intents" in app_command and "public.deck_intents" in app_command
    assert "Refusing to start: database connection or schema check failed" in app_command
    worker_command = " ".join(services["bracket-worker"]["command"])
    assert "DATA_ACCESS_MODE" in worker_command and "!= readwrite" in worker_command
    for name in ("db", "redis", "app"):
        assert services[name]["healthcheck"]["test"]
    for name in ("db", "redis"):
        assert services["app"]["depends_on"][name]["condition"] == "service_healthy"
    assert services["web"]["depends_on"]["app"]["condition"] == "service_healthy"
    assert services["bracket-worker"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["bracket-worker"]["depends_on"]["app"]["condition"] == "service_healthy"


def test_workers_are_disabled_by_default():
    result = render(include_workers=False)
    assert result.returncode == 0, result.stderr
    assert "bracket-worker" not in json.loads(result.stdout)["services"]
    assert "web" not in json.loads(result.stdout)["services"]


@pytest.mark.parametrize("value", [None, ""])
def test_database_url_required(value):
    result = render(value)
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


def test_optional_endpoints_and_secret_interpolation(config):
    app_env = config["services"]["app"]["environment"]
    assert app_env["DATABASE_URL"] == TEST_URL
    assert app_env["ORACLE_URL"] == app_env["FORGE_SIM_URL"] == ""
    assert app_env["ORACLE_KEY"] == app_env["OPENROUTER_API_KEY"] == ""
    digest = "sha256:" + "a" * 64
    result = render(APP_IMAGE_DIGEST=digest, ORACLE_URL="https://oracle.example", FORGE_SIM_URL="https://forge.example")
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["app"]["image"].endswith("@" + digest)
    assert services["bracket-worker"]["image"].endswith("@" + digest)
    assert services["app"]["environment"]["FORGE_SIM_URL"] == "https://forge.example"
    assert services["app"]["environment"]["ORACLE_URL"] == "https://oracle.example"
    result = render(DATA_ACCESS_MODE="readwrite")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["services"]["app"]["environment"]["DATA_ACCESS_MODE"] == "readwrite"


def test_runbook_warns_about_secrets_dns_and_bind_assumptions():
    doc = (ROOT / "docs/COMPUTE_GITOPS_RECOVERY.md").read_text()
    assert "DATABASE_URL" in doc and "URL-encode" in doc
    assert "expose secrets" in doc and "never commit credentials" in doc
    assert "DNS" in doc and "shared Docker network" in doc
    assert "auth" in doc and "original stack snapshot" in doc
    assert "AOF" in doc and "dump.rdb" in doc
    assert "a013_deck_intents" in doc and "approval" in doc


@pytest.mark.parametrize("service,original,filename,contents", [
    ("db", "/var/lib/postgresql/data/PG_VERSION", "PG_VERSION", "17\n"),
    ("redis", "/data/dump.rdb", "dump.rdb", "snapshot"),
])
def test_storage_guards_fail_closed_and_pass_existing_data(config, tmp_path, service, original, filename, contents):
    entrypoint = config["services"][service]["entrypoint"]
    # `compose config` re-escapes literal dollars for a second Compose pass.
    script = entrypoint[2].replace("$$", "$").replace(original, str(tmp_path / filename))
    fake = tmp_path / "docker-entrypoint.sh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    def run():
        return subprocess.run(["/bin/sh", "-ec", script], env=env, capture_output=True)

    assert run().returncode != 0
    (tmp_path / filename).write_text("")
    assert run().returncode != 0
    if service == "db":
        (tmp_path / filename).write_text("16\n")
        assert run().returncode != 0
    (tmp_path / filename).write_text(contents)
    assert run().returncode == 0


@pytest.mark.parametrize("revisions,table,database,connection_error,starts", [
    (["a013_deck_intents"], True, "commander_lab", False, True),
    (["a012_app_tables_to_public"], True, "commander_lab", False, False),
    (["another_revision"], True, "commander_lab", False, False),
    ([], True, "commander_lab", False, False),
    (["a013_deck_intents", "another_revision"], True, "commander_lab", False, False),
    (["a013_deck_intents"], False, "commander_lab", False, False),
    (["a013_deck_intents"], True, "wrong_database", False, False),
    (["a013_deck_intents"], True, "commander_lab", True, False),
])
def test_app_schema_gate_offline(config, tmp_path, revisions, table, database, connection_error, starts):
    # Python launched by the actual Compose command imports these fakes instead of
    # SQLAlchemy or the application, making real database access impossible.
    fake_config = tmp_path / "src/server/infra/db/config.py"
    fake_config.parent.mkdir(parents=True)
    for package in ("src", "src/server", "src/server/infra", "src/server/infra/db"):
        (tmp_path / package / "__init__.py").write_text("")
    fake_config.write_text(
        "PG_CONNECT_ARGS = {'application_name': 'offline-test'}\n"
        "def normalize_url(url):\n"
        "    return 'normalized:' + url\n"
    )
    (tmp_path / "sqlalchemy.py").write_text('''\
import json
import os

def text(query):
    return query

class Result:
    def __init__(self, value):
        self.value = value
    def scalars(self):
        return self
    def all(self):
        return self.value
    def scalar_one(self):
        return self.value

class Connection:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, query):
        with open(os.environ['QUERY_LOG'], 'a') as log:
            log.write(query + '\\n')
        values = {
            'SELECT version_num FROM public.alembic_version': json.loads(os.environ['REVISIONS']),
            "SELECT to_regclass('public.deck_intents') IS NOT NULL": json.loads(os.environ['TABLE_EXISTS']),
            'SELECT current_database()': os.environ['DB_NAME'],
        }
        return Result(values[query])

class Engine:
    def connect(self):
        if os.environ['CONNECTION_ERROR'] == '1':
            raise RuntimeError('connection failed: ' + os.environ['DATABASE_URL'])
        return Connection()
    def dispose(self):
        pass

def create_engine(url, connect_args):
    assert url == 'normalized:' + os.environ['DATABASE_URL']
    assert connect_args == {'application_name': 'offline-test', 'connect_timeout': 5}
    return Engine()
''')
    uvicorn = tmp_path / "uvicorn"
    uvicorn.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$START_LOG"\n')
    uvicorn.chmod(0o755)
    secret = "sensitive-password-for-test"
    database_url = f"postgresql://user:{secret}@db/commander_lab"
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PYTHONPATH": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": database_url,
        "REVISIONS": json.dumps(revisions),
        "TABLE_EXISTS": json.dumps(table),
        "DB_NAME": database,
        "CONNECTION_ERROR": "1" if connection_error else "0",
        "QUERY_LOG": str(tmp_path / "queries"),
        "START_LOG": str(tmp_path / "started"),
    }
    command = config["services"]["app"]["command"]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is starts
    assert secret not in result.stdout + result.stderr
    assert database_url not in result.stdout + result.stderr
    assert (tmp_path / "started").exists() is starts
    if starts:
        assert (tmp_path / "started").read_text().splitlines() == [
            "src.server.app.main:app", "--host", "0.0.0.0", "--port", "8000",
        ]
    else:
        assert result.stderr.strip() == "Refusing to start: database connection or schema check failed"
    if not connection_error:
        assert (tmp_path / "queries").read_text().splitlines() == [
            "SELECT version_num FROM public.alembic_version",
            "SELECT to_regclass('public.deck_intents') IS NOT NULL",
            "SELECT current_database()",
        ]


@pytest.mark.parametrize("mode,starts", [("readonly", False), ("readwrite", True)])
def test_worker_access_mode_guard_offline(config, tmp_path, mode, starts):
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$START_LOG"\n')
    python.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DATA_ACCESS_MODE": mode,
        "START_LOG": str(tmp_path / "started"),
    }
    command = config["services"]["bracket-worker"]["command"]
    # Compose config re-escapes literal dollars for a second Compose pass.
    command = [*command[:2], command[2].replace("$$", "$")]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is starts
    assert (tmp_path / "started").exists() is starts
    if starts:
        assert (tmp_path / "started").read_text().splitlines() == [
            "-m", "src.server.domain.analytics.bracket_worker",
        ]
    else:
        assert "Refusing to start bracket worker" in result.stderr

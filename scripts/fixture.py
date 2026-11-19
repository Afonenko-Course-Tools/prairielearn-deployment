#!/usr/bin/env python3
"""Первичная связка отдельной локальной Moodle/PrairieLearn fixture."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess  # nosec B404 # fixed CLI argument lists, no shell
import sys
from typing import Any

import fixture_exam

ROOT = Path(__file__).resolve().parents[1]
SLUG = r"[a-z][a-z0-9-]{0,62}"
DNS_NAME = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
BRIDGE_BIND_HOST = "0.0.0.0"  # nosec B104 # private Compose network, no published port


def capture(command: list[str]) -> str:
    return subprocess.run(  # nosec B603 # explicit argv; commands are local operator tools
        command, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE
    ).stdout


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)  # nosec B603 # explicit CLI argv


def absolute(values: dict[str, str], name: str, *, strict: bool = True) -> Path:
    path = Path(values.get(name, ""))
    if not path.is_absolute():
        raise ValueError(f"Укажите абсолютный путь {name}")
    return path.resolve(strict=strict)


def exact(value: str, pattern: str, label: str) -> str:
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Некорректное значение {label}")
    return value


def local_port(values: dict[str, str], name: str) -> int:
    result = int(exact(values.get(name, ""), r"[0-9]{4,5}", name))
    if not 1024 <= result <= 65535:
        raise ValueError(f"Укажите непривилегированный порт {name}")
    return result


def local_tcp_host(values: dict[str, str], name: str) -> str:
    return exact(values.get(name, ""), DNS_NAME, name)


def atomic_text(path: Path, data: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(data)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def ensure_value(path: Path, value: Any) -> None:
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"Сохранённая привязка изменилась: {path.name}")
    else:
        atomic_json(path, value)


def token(path: Path) -> str:
    if not path.exists():
        atomic_text(path, secrets.token_hex(32) + "\n")
    return exact(path.read_text().strip(), r"[0-9a-f]{64}", path.name)


def bind_project(project: str, directory: Path, *, creating: bool) -> None:
    """Немонтируемый Docker volume закрепляет имя проекта на этом Docker-хосте."""
    claim = project + "_fixture_owner"
    label = "com.prairielearn.fixture.directory"
    if creating:
        project_label = f"label=com.docker.compose.project={project}"
        for resource in ("container", "volume", "network"):
            flags = ["--all"] if resource == "container" else []
            if capture(
                ["docker", resource, "ls", *flags, "--filter", project_label, "--quiet"]
            ).strip():
                raise ValueError("Compose project уже используется, укажите другое имя")
        capture(
            ["docker", "volume", "create", "--label", f"{label}={directory}", claim]
        )
    actual = capture(
        [
            "docker",
            "volume",
            "inspect",
            "--format",
            '{{index .Labels "' + label + '"}}',
            claim,
        ]
    ).rstrip("\n")
    if actual != str(directory):
        raise ValueError("Имя Compose project закреплено за другим каталогом fixture")


def native_record(registry: Path, native: Path) -> dict[str, Any]:
    data = json.loads(registry.read_text())
    if data.get("schema") != "pl-courses-v2" or len(data.get("courses", [])) != 1:
        raise ValueError("Для fixture нужен реестр Platform с одним курсом")
    record = data["courses"][0]
    exact(record["id"], SLUG, "ключ курса")
    commit = exact(record["commit"], r"[0-9a-f]{40}", "commit Native")
    git = ["git", "-C", str(native)]
    if (
        Path(capture([*git, "rev-parse", "--show-toplevel"]).strip()) != native
        or capture([*git, "rev-parse", "HEAD"]).strip() != commit
        or capture(
            [*git, "status", "--porcelain", "--ignored", "--untracked-files=all"]
        )
    ):
        raise ValueError("Нужен чистый checkout указанного Native commit")
    delivery = json.loads((native / "delivery.json").read_text())
    if (
        delivery.get("courseId") != record["id"]
        or len(delivery.get("instances", {})) != 1
    ):
        raise ValueError("Для fixture нужен один экземпляр Native-курса из реестра")
    return record


def bind_native(
    compose: list[str], key: str, actor: str, instance: str
) -> dict[str, Any]:
    common = [
        "--path",
        f"/course/{key}/current",
        "--actor-uid",
        actor,
        "--registry",
        "/run/deployment/courses.json",
        "--course-key",
        key,
    ]
    node = [*compose, "exec", "-T", "-w", "/PrairieLearn", "pl", "node"]
    tools = "/PrairieLearn/apps/prairielearn/tools"
    bootstrap = json.loads(
        capture([*node, f"{tools}/bootstrap-native-course.mjs", *common]).splitlines()[
            -1
        ]
    )
    course = exact(str(bootstrap["courseId"]), r"[1-9][0-9]*", "Native courseId")
    sync = json.loads(
        capture(
            [*node, f"{tools}/sync-native-course.mjs", "--course-id", course, *common]
        ).splitlines()[-1]
    )
    rows = sync["instances"]
    if (
        len(rows) != 1
        or rows[0]["short_name"] != instance
        or str(rows[0]["course_id"]) != course
        or rows[0]["deleted_at"] is not None
    ):
        raise ValueError("Platform вернула другой экземпляр Native-курса")
    return {
        "courseKey": key,
        "instance": instance,
        "courseId": course,
        "courseInstanceId": exact(
            str(rows[0]["id"]), r"[1-9][0-9]*", "Native courseInstanceId"
        ),
        "courseInstanceUuid": exact(
            rows[0]["uuid"],
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
            "Native instance UUID",
        ),
    }


def link_services(
    directory: Path,
    binding: dict[str, Any],
    registration: dict[str, Any],
    pl_origin: str,
    moodle_origin: str,
    exam: fixture_exam.Exam | None,
) -> None:
    key, instance = binding["courseKey"], binding["instance"]
    registration = registration["registration"]
    if registration["issuer"] != moodle_origin:
        raise ValueError("Moodle вернула другой публичный issuer")
    gateway, bridge = directory / "gateway", directory / "bridge"
    bridge_key = token(bridge / "bridge-token")
    handoff_key = token(gateway / "handoff-token")
    atomic_text(gateway / "bridge-token", bridge_key + "\n")
    atomic_text(
        gateway / "handoff-auth.conf",
        f'proxy_set_header Authorization "Bearer {handoff_key}";\n',
    )
    ensure_value(
        bridge / "config.json",
        {
            "listenHost": BRIDGE_BIND_HOST,
            "listenPort": 3001,
            "tokenFile": "/run/pl-gateway/bridge-token",
            "administratorEmail": None,
            "bindings": [binding],
        },
    )
    course = {
        "publicOrigin": pl_origin,
        "routePrefix": f"/gateway/courses/{key}",
        "database": "/var/lib/gateway/auth.sqlite",
        "handoffTokenFile": "/run/config/handoff-token",
        "bridge": {"url": "http://pl:3001", "tokenFile": "/run/config/bridge-token"},
        "courseKey": key,
        "instance": instance,
        "registration": {
            **{
                name: registration[name]
                for name in (
                    "issuer",
                    "clientId",
                    "deploymentId",
                    "contextId",
                    "authUrl",
                    "jwksUrl",
                )
            },
            "privateJwksUrl": "http://moodle/mod/lti/certs.php",
            "activities": {
                "catalog": {
                    "resourceLinkId": registration["activities"]["catalog"][
                        "resourceLinkId"
                    ]
                }
            },
        },
    }
    if exam:
        proxy_token = token(gateway / "exam-proxy-token")
        atomic_text(
            gateway / "exam-proxy-token.conf",
            f'proxy_set_header X-Exam-Proxy-Token "{proxy_token}";\n',
        )
        course["examAudit"] = {
            "examId": exam.exam_id,
            "activities": ["catalog"],
            "database": "/var/lib/gateway/exam.sqlite",
            "proxyTokenFile": "/run/config/exam-proxy-token",
            "classroomCidrs": [exam.client_cidr],
            "restrictLaunchToClassroom": True,
        }
    ensure_value(
        gateway / "runtime.json", {"schemaVersion": 2, "courses": {key: course}}
    )


def fixture(action: str, env_file: Path, kind: str) -> None:
    base = ["docker", "compose", "--env-file", str(env_file)]
    inputs = dict(
        line.split("=", 1)
        for line in capture(
            [*base, "-f", "compose.yml", "config", "--environment"]
        ).splitlines()
        if "=" in line
    )
    project = exact(
        inputs.get("COMPOSE_PROJECT_NAME", ""), SLUG, "COMPOSE_PROJECT_NAME"
    )
    if project == "prairielearn-local":
        raise ValueError("Для fixture нужно отдельное имя Compose project")
    directory = absolute(inputs, "FIXTURE_DIR")
    if (
        os.getuid() == 0
        or directory.stat().st_uid != os.getuid()
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
    ):
        raise ValueError(
            "Запустите fixture от непривилегированного владельца FIXTURE_DIR с правами 0700"
        )
    marker = directory / "fixture.json"
    native, registry = (
        absolute(inputs, name, strict=action == "up")
        for name in ("FIXTURE_NATIVE_DIR", "FIXTURE_REGISTRY")
    )
    if action == "up":
        record = native_record(registry, native)
    else:
        record = json.loads(marker.read_text())["record"]
    key = exact(record["id"], SLUG, "ключ курса")
    pl_host, moodle_host = (
        local_tcp_host(inputs, name) for name in ("PL_HOST", "MOODLE_HOST")
    )
    pl_port, moodle_port = (
        local_port(inputs, name) for name in ("PL_HTTPS_PORT", "MOODLE_HTTPS_PORT")
    )
    if pl_host == moodle_host or pl_port == moodle_port:
        raise ValueError("Для PL и Moodle требуются разные имена и порты")
    pl_origin, moodle_origin = (
        f"https://{pl_host}:{pl_port}",
        f"https://{moodle_host}:{moodle_port}",
    )
    state = directory / "state"
    publish_ip = fixture_exam.publication(inputs)
    server = publish_ip != fixture_exam.LOOPBACK
    if (
        server
        and kind == "ordinary"
        and (pl_port, moodle_port) != fixture_exam.SERVER_ORDINARY_PORTS
    ):
        raise ValueError(
            "Для серверного обычного контроля PL требует порт 21646, Moodle — 21645"
        )
    env = {
        "STATE_DIR": str(state),
        "HOST_JOBS_DIR": str(directory / "jobs"),
        "BIND_IP": str(publish_ip if kind == "ordinary" else fixture_exam.LOOPBACK),
        "FIXTURE_WEB_CLIENT_CIDR": (
            str(fixture_exam.SERVER_CLIENTS) if server and kind == "ordinary" else ""
        ),
        "FIXTURE_COURSE_KEY": key,
        "FIXTURE_NATIVE_DIR": str(native),
        "FIXTURE_REGISTRY": str(registry),
        "MOODLE_TOOL_SLUG": key,
        "PL_CONFIG_FILE": str(state / "pl/config.json"),
        "BRIDGE_CONFIG_DIR": str(directory / "bridge"),
        "GATEWAY_CONFIG_DIR": str(directory / "gateway"),
        "GATEWAY_RUN_AS": f"{os.getuid()}:{os.getgid()}",
    }
    exam = (
        fixture_exam.settings(directory, inputs, publish_ip) if kind == "exam" else None
    )
    if exam:
        env["EXAM_PROJECT_NAME"] = project
        env.update(exam.environment)
    os.environ.update(env)
    stage = [
        *base,
        "-p",
        project,
        "-f",
        "compose.yml",
        "-f",
        "deploy/fixture-native.yml",
    ]
    common = [*stage, "-f", "compose.auth.yml"]
    final = (
        [*common, "-f", "deploy/exam-fixture.yml", "--profile", "exam"]
        if exam
        else common
    )
    identity: dict[str, Any] = {
        "project": project,
        "kind": kind,
        "record": record,
        "native": str(native),
        "registry": str(registry),
        "origins": [pl_origin, moodle_origin],
        "exam": exam.identity if exam else None,
    }
    if server:
        identity["serverIP"] = str(publish_ip)

    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not marker.exists():
            if action != "up" or any(
                path.name != ".lock" for path in directory.iterdir()
            ):
                raise ValueError("Для новой fixture нужен пустой отдельный FIXTURE_DIR")
        bind_project(project, directory, creating=not marker.exists())
        ensure_value(marker, identity)
        if action == "status":
            run([*final, "ps", "--all"])
            return
        if action == "down":
            run([*final, "down"])
            return

        for path in (
            state,
            directory / "jobs",
            directory / "bridge",
            directory / "gateway",
        ):
            path.mkdir(exist_ok=True)
        run([*final, "config", "--quiet"])
        run(
            [
                *stage,
                "up",
                "--no-deps",
                "--exit-code-from",
                "config-init",
                "config-init",
            ]
        )
        run([*common, "up", "-d", "--wait", "pl", "moodle"])
        run(
            [
                *common,
                "up",
                "--no-deps",
                "--exit-code-from",
                "bootstrap-admin",
                "bootstrap-admin",
            ]
        )
        actor = inputs.get("BOOTSTRAP_ADMIN_UID", "servicebootstrap@local.invalid")
        instance = next(
            iter(json.loads((native / "delivery.json").read_text())["instances"])
        )
        binding = bind_native(common, key, actor, instance)
        registration = json.loads((state / "moodle/registration.json").read_text())
        link_services(
            directory,
            binding,
            registration,
            pl_origin,
            moodle_origin,
            exam,
        )
        if exam:
            fixture_exam.configure(
                exam,
                state=state,
                pl_origin=pl_origin,
                moodle_origin=moodle_origin,
                key=key,
                registration=registration,
            )
        run([*final, "up", "-d", "--wait"])
        print(f"Moodle LTI: {registration['moodle']['activityUrl']}")
        print(
            f"PrairieLearn: {pl_origin}/pl/course_instance/{binding['courseInstanceId']}/assessments"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "status", "down"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--kind", choices=("exam", "ordinary"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        fixture(args.action, args.env_file.resolve(strict=True), args.kind)
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Fixture остановлена: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

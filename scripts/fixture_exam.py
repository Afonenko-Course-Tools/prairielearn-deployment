"""Подготавливает Squid и доверенный Nginx для отдельной экзаменационной fixture."""

from dataclasses import dataclass
from ipaddress import IPv4Network
from pathlib import Path
import re
import subprocess  # nosec B404 # fixed operator CLI argv, no shell
from typing import Any
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Exam:
    directory: Path
    exam_id: str
    client: str
    nginx: str
    squid: str
    subnet: IPv4Network
    host_port: int
    image: str

    @property
    def identity(self) -> dict[str, str | int]:
        return {"id": self.exam_id, "subnet": str(self.subnet), "port": self.host_port}

    @property
    def environment(self) -> dict[str, str]:
        return {
            "EXAM_NGINX_CONF_DIR": str(self.directory / "nginx"),
            "EXAM_SQUID_CONFIG": str(self.directory / "squid.conf"),
            "EXAM_POLICY_DIR": str(self.directory / "policy"),
            "EXAM_TLS_DIR": str(self.directory / "tls"),
            "EXAM_STATE_DIR": str(self.directory / "state"),
            "EXAM_PROXY_PUBLISH_PORT": str(self.host_port),
            "EXAM_INGRESS_SUBNET": str(self.subnet),
            "EXAM_FIXTURE_GATEWAY": self.client,
            "EXAM_FIXTURE_NGINX_IP": self.nginx,
            "EXAM_FIXTURE_SQUID_IP": self.squid,
        }


def settings(directory: Path, inputs: dict[str, str]) -> Exam:
    name = inputs.get("EXAM_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        raise ValueError("Нужен EXAM_ID для регистрации экзамена в Gateway")
    address = IPv4Network(inputs.get("EXAM_INGRESS_SUBNET", ""))
    if address.prefixlen != 29 or not any(
        address.subnet_of(IPv4Network(private_net))
        for private_net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ValueError(
            "Укажите свободную частную IPv4 подсеть /29 для EXAM_INGRESS_SUBNET"
        )
    port = int(inputs.get("EXAM_PROXY_PUBLISH_PORT", "0"))
    if not 1024 <= port <= 65535:
        raise ValueError("Нужен локальный непривилегированный EXAM_PROXY_PUBLISH_PORT")
    image = inputs.get("EXAM_PROXY_IMAGE", "")
    if not image or image.startswith("-"):
        raise ValueError("Укажите проверенный EXAM_PROXY_IMAGE")
    if not inputs.get("GATEWAY_IMAGE"):
        raise ValueError("Укажите проверенный GATEWAY_IMAGE с поддержкой examAudit")
    return Exam(
        directory / "squid",
        name,
        str(address[1]),
        str(address[2]),
        str(address[3]),
        address,
        port,
        image,
    )


def replacing(source: str, old: str, new: str, count: int = 1) -> str:
    if source.count(old) != count:
        raise ValueError("Изменился шаблон экзаменационного ingress")
    return source.replace(old, new)


def render_nginx(exam: Exam, state: Path) -> None:
    template = (state / "nginx-auth.conf").read_text()
    template = replacing(
        template, "http {", "http {\n  include /etc/nginx/exam/ingress.conf;"
    )
    template = replacing(
        template,
        "  server {",
        "  server {\n    if ($exam_ingress_allowed = 0) { return 403; }",
        count=2,
    )
    template = replacing(
        template,
        "    location /gateway/ {",
        "    location /gateway/ {\n"
        "      include /etc/nginx/private/exam-proxy-token.conf;\n"
        "      proxy_set_header X-Exam-Client-IP $exam_verified_client_ip;",
    )
    ingress = (
        (ROOT / "deploy/exam-client-ip.nginx.conf")
        .read_text()
        .replace("192.0.2.10", exam.squid)
    )
    ingress += f"""
map $realip_remote_addr $exam_squid_peer {{
    default 0;
    {exam.squid} 1;
}}
map "$exam_squid_peer|$realip_remote_addr|$uri" $exam_ingress_allowed {{
    default 0;
    ~^1\\| 1;
    "0|127.0.0.1|/healthz" 1;
}}
"""
    (exam.directory / "nginx/nginx.conf").write_text(template)
    (exam.directory / "nginx/ingress.conf").write_text(ingress)


def render_squid(
    exam: Exam,
    pl_origin: str,
    moodle_origin: str,
    key: str,
    registration: dict[str, Any],
) -> None:
    pl, moodle = urlsplit(pl_origin), urlsplit(moodle_origin)
    config = (ROOT / "deploy/exam-squid.conf").read_text()
    config = replacing(
        config, "http_port 127.0.0.1:18080 ", f"http_port {exam.squid}:20580 "
    )
    for extension in ("crt", "key"):
        config = replacing(
            config,
            f"/run/exam-tls/issuer.{extension}",
            f"/run/exam-tls/issuer/issuer.{extension}",
        )
    config = replacing(
        config,
        "acl exam_clients src 127.0.0.1/32",
        f"acl exam_clients src {exam.client}/32",
    )
    config = replacing(
        config, "acl tls_port port 443", f"acl tls_port port {pl.port} {moodle.port}"
    )
    config = replacing(
        config,
        "http_access allow encrypted https_scheme moodle_name host_moodle client_moodle moodle_ip",
        "include /etc/squid/exam-policy/moodle-auth-only.conf",
    )
    (exam.directory / "squid.conf").write_text(
        (ROOT / "deploy/exam-identity-squid.conf").read_text()
    )
    policy = exam.directory / "policy"
    (policy / "squid-base.conf").write_text(config)
    destinations = []
    for app, origin in (("pl", pl), ("moodle", moodle)):
        # Точки экранируются для POSIX regex Squid; hostname уже проверен.
        authority = origin.netloc.replace(".", "[.]")
        destinations.append(
            f"acl {app}_ip dst {exam.nginx}/32\n"
            f"acl {app}_name dstdomain -n {origin.hostname}\n"
            f"acl connect_{app} url_regex -i ^{authority}$\n"
            f"acl host_{app} req_header Host -i ^{authority}$\n"
            f"acl client_{app} ssl::server_name --client-requested {origin.hostname}\n"
        )
    (policy / "destinations.conf").write_text("\n".join(destinations))
    (policy / "hosts").write_text(
        f"{exam.nginx} {pl.hostname}\n{exam.nginx} {moodle.hostname}\n"
    )
    client_id = registration["registration"]["clientId"]
    module_id = str(registration["moodle"]["courseModuleId"])
    if not re.fullmatch(r"[A-Za-z0-9_-]+", client_id) or not re.fullmatch(
        r"[1-9][0-9]*", module_id
    ):
        raise ValueError("Moodle вернула неподдерживаемые идентификаторы ACL")
    target = f"{pl_origin}/gateway/courses/{key}/lti/launch"
    exact_targets = "|".join(
        item.replace(".", "[.]") for item in (target, quote(target, safe=""))
    )
    template = (ROOT / "deploy/exam-policy/moodle-auth-only.conf.example").read_text()
    template = replacing(template, "MOODLE_COURSE_MODULE_ID", module_id)
    template = replacing(template, "MOODLE_OIDC_CLIENT_ID", client_id)
    template = replacing(template, "MOODLE_OIDC_TARGET_REGEX", f"({exact_targets})")
    (policy / "moodle-auth-only.conf").write_text(template)


def configure(
    exam: Exam,
    *,
    state: Path,
    pl_origin: str,
    moodle_origin: str,
    key: str,
    registration: dict[str, Any],
) -> None:
    for path in (
        exam.directory,
        exam.directory / "nginx",
        exam.directory / "policy",
        exam.directory / "tls",
        exam.directory / "state",
    ):
        path.mkdir(exist_ok=True)
    render_nginx(exam, state)
    render_squid(exam, pl_origin, moodle_origin, key, registration)
    # Пользователь 13 читает policy, контейнер видит её через отдельный bind.
    (exam.directory / "policy").chmod(0o755)
    (exam.directory / "squid.conf").chmod(0o644)
    for policy in (exam.directory / "policy").iterdir():
        policy.chmod(0o644)
    mounts = {
        exam.directory / "tls": "/run/exam-tls",
        exam.directory / "state": "/var/lib/exam-squid",
        exam.directory / "policy": "/etc/squid/exam-policy,readonly",
        exam.directory / "squid.conf": "/etc/squid/squid.conf,readonly",
        state / "tls/ca.crt": "/run/nginx-ca.crt,readonly",
        ROOT / "scripts/fixture-squid-init.sh": "/run/fixture-squid-init.sh,readonly",
    }
    command = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--user",
        "0:0",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    for cap in ("CHOWN", "FOWNER", "DAC_OVERRIDE", "SETUID", "SETGID"):
        command.extend(["--cap-add", cap])
    for source, target in mounts.items():
        command.extend(["--mount", f"type=bind,src={source},dst={target}"])
    result = subprocess.run(  # nosec B603 # explicit local CLI argv
        [*command, "--entrypoint", "/bin/sh", exam.image, "/run/fixture-squid-init.sh"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    ca = exam.directory.parent / "squid-ca.pem"
    if ca.exists():
        if ca.read_bytes() != result.stdout:
            raise ValueError(
                "Публичный CA Squid изменился; сохранённый экспорт не заменён"
            )
    else:
        temporary_ca = ca.with_suffix(".pem.tmp")
        temporary_ca.write_bytes(result.stdout)
        temporary_ca.replace(ca)
    der = ca.with_suffix(".cer")
    temporary_der = der.with_suffix(".cer.tmp")
    subprocess.run(  # nosec B603, B607 # installed OpenSSL argv
        [
            "openssl",
            "x509",
            "-in",
            str(ca),
            "-outform",
            "DER",
            "-out",
            str(temporary_der),
        ],
        check=True,
    )
    temporary_der.replace(der)

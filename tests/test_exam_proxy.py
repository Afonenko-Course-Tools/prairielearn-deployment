"""Штатный Compose config: optional proxy не получает state/dependencies fixture."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess  # nosec B404 # fixed Compose config only; no daemon or shell
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fixture_exam", ROOT / "scripts/fixture_exam.py"
)
assert SPEC is not None and SPEC.loader is not None  # nosec B101 # test-only loader invariant
fixture_exam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_exam)


class ExamProxyComposeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="exam-compose-")
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "DOCKER_CONFIG")
        }
        self.inputs = {
            "EXAM_PROJECT_NAME": "isolated-exam-test",
            "EXAM_PROXY_IMAGE": "example.invalid/squid@sha256:" + "4" * 64,
            "EXAM_SQUID_CONFIG": str(directory / "squid.conf"),
            "EXAM_POLICY_DIR": str(directory / "policy"),
            "EXAM_TLS_DIR": str(directory / "tls"),
            "EXAM_STATE_DIR": str(directory / "private-state"),
        }
        self.environment.update(self.inputs)
        self.environment["STATE_DIR"] = str(directory / "forbidden-fixture-state")

    def compose(self, environment=None, files=("deploy/exam-compose.yml",)):
        return subprocess.run(  # nosec B603, B607 # fixed config argv and synthetic paths, no daemon
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--profile",
                "exam",
                *(part for name in files for part in ("-f", name)),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=self.environment if environment is None else environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_standalone_graph_cannot_inherit_fixture_or_docker_socket(self):
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["name"], self.inputs["EXAM_PROJECT_NAME"])
        self.assertEqual(set(config["services"]), {"exam-proxy"})
        self.assertFalse(config.get("volumes"))
        proxy = config["services"]["exam-proxy"]
        self.assertEqual(proxy["profiles"], ["exam"])
        self.assertEqual(proxy["image"], self.inputs["EXAM_PROXY_IMAGE"])
        self.assertEqual(proxy["user"], "13:13")
        self.assertEqual(proxy["network_mode"], "host")
        self.assertTrue(proxy["read_only"])
        self.assertEqual(proxy["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", proxy["security_opt"])
        self.assertFalse(proxy.get("ports"))
        self.assertFalse(proxy.get("depends_on"))
        mounts = {mount["target"]: mount for mount in proxy["volumes"]}
        expected = {
            "/etc/squid/squid.conf": "EXAM_SQUID_CONFIG",
            "/etc/squid/exam-policy": "EXAM_POLICY_DIR",
            "/run/exam-tls": "EXAM_TLS_DIR",
            "/var/lib/exam-squid": "EXAM_STATE_DIR",
        }
        self.assertEqual(set(mounts), set(expected))
        for target, variable in expected.items():
            mount = mounts[target]
            self.assertEqual(mount["source"], self.inputs[variable])
            self.assertEqual(mount["type"], "bind")
            self.assertIs(mount["bind"]["create_host_path"], False)
            self.assertEqual(
                mount.get("read_only", False), target != "/var/lib/exam-squid"
            )

    def test_each_missing_or_empty_required_input_is_refused(self):
        for variable in self.inputs:
            for value in (None, ""):
                with self.subTest(variable=variable, value=value):
                    environment = dict(self.environment)
                    if value is None:
                        del environment[variable]
                    else:
                        environment[variable] = value
                    result = self.compose(environment)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(variable, result.stderr)

    def test_fixture_publishes_only_squid_and_keeps_native_mounts_read_only(self):
        directory = Path(self.temporary.name)
        environment = dict(
            self.environment,
            COMPOSE_PROJECT_NAME=self.inputs["EXAM_PROJECT_NAME"],
            EXAM_NGINX_CONF_DIR=str(directory / "exam-nginx"),
            EXAM_INGRESS_SUBNET="172.22.83.0/29",
            EXAM_FIXTURE_GATEWAY="172.22.83.1",
            EXAM_FIXTURE_NGINX_IP="172.22.83.2",
            EXAM_FIXTURE_SQUID_IP="172.22.83.3",
            EXAM_PROXY_PUBLISH_PORT="21680",
            FIXTURE_NATIVE_DIR=str(directory / "native"),
            FIXTURE_REGISTRY=str(directory / "registry.json"),
            FIXTURE_COURSE_KEY="java-test",
            PL_CONFIG_FILE=str(directory / "native-config.json"),
            BRIDGE_CONFIG_DIR=str(directory / "bridge"),
            GATEWAY_CONFIG_DIR=str(directory / "gateway"),
            GATEWAY_RUN_AS="1575:1576",
            PL_HOST="pl.example.test",
            PL_HTTPS_PORT="21644",
        )
        files = ("compose.yml", "deploy/fixture-native.yml", "compose.auth.yml")
        result = self.compose(environment, files=(*files, "deploy/exam-fixture.yml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads(result.stdout)
        services = model["services"]
        self.assertEqual(
            {name for name, service in services.items() if service.get("ports")},
            {"exam-proxy"},
        )
        squid = services["exam-proxy"]
        published = squid["ports"][0]
        self.assertEqual(
            (published["host_ip"], published["published"], published["target"]),
            ("127.0.0.1", "21680", 20580),
        )
        self.assertNotIn("network_mode", squid)
        self.assertEqual(
            squid["networks"], {"exam-ingress": {"ipv4_address": "172.22.83.3"}}
        )
        self.assertEqual(
            model["networks"]["exam-ingress"]["ipam"]["config"],
            [{"subnet": "172.22.83.0/29", "gateway": "172.22.83.1"}],
        )
        native = {mount["target"]: mount for mount in services["pl"]["volumes"]}
        self.assertTrue(native["/course/java-test/current"]["read_only"])
        self.assertTrue(native["/run/deployment/courses.json"]["read_only"])
        self.assertEqual(
            services["config-init"]["environment"]["FIXTURE_NATIVE_LTI"], "1"
        )
        self.assertEqual(services["gateway"]["user"], "1575:1576")
        self.assertEqual(
            services["gateway-data-init"]["environment"]["GATEWAY_DATA_OWNER"],
            "1575:1576",
        )

        environment["EXAM_FIXTURE_PUBLISH_IP"] = "10.135.250.231"
        server = self.compose(environment, files=(*files, "deploy/exam-fixture.yml"))
        self.assertEqual(server.returncode, 0, server.stderr)
        ports = {
            name: service["ports"]
            for name, service in json.loads(server.stdout)["services"].items()
            if service.get("ports")
        }
        self.assertEqual(set(ports), {"exam-proxy"})
        self.assertEqual(len(ports["exam-proxy"]), 1)
        self.assertEqual(ports["exam-proxy"][0]["host_ip"], "10.135.250.231")

        ordinary = self.compose(
            dict(
                environment,
                BIND_IP="10.135.250.231",
                FIXTURE_WEB_CLIENT_CIDR="10.0.0.0/8",
            ),
            files=files,
        )
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        ordinary_services = json.loads(ordinary.stdout)["services"]
        self.assertEqual(
            {
                name
                for name, service in ordinary_services.items()
                if service.get("ports")
            },
            {"proxy"},
        )
        self.assertEqual(
            {port["host_ip"] for port in ordinary_services["proxy"]["ports"]},
            {"10.135.250.231"},
        )
        self.assertIn(
            "pl.example.test:21644:127.0.0.1:21644",
            ordinary_services["proxy"]["healthcheck"]["test"],
        )


class FixtureSourceTests(unittest.TestCase):
    def test_explicit_server_address_and_client_policy(self):
        inputs = {
            "EXAM_ID": "exam-pilot",
            "EXAM_INGRESS_SUBNET": "172.16.252.0/29",
            "EXAM_PROXY_PUBLISH_PORT": "21680",
            "EXAM_PROXY_IMAGE": "example.invalid/squid:tested",
            "GATEWAY_IMAGE": "example.invalid/gateway:tested",
        }
        local = fixture_exam.settings(ROOT, inputs, fixture_exam.publication(inputs))
        server_inputs = dict(
            inputs, FIXTURE_SCOPE="server", FIXTURE_SERVER_IP="10.135.250.231"
        )
        server = fixture_exam.settings(
            ROOT, server_inputs, fixture_exam.publication(server_inputs)
        )
        self.assertEqual(local.environment["EXAM_FIXTURE_PUBLISH_IP"], "127.0.0.1")
        self.assertEqual(local.client_cidr, "172.16.252.1/32")
        self.assertEqual(
            server.environment["EXAM_FIXTURE_PUBLISH_IP"], "10.135.250.231"
        )
        self.assertEqual(server.client_cidr, "10.0.0.0/8")
        for invalid in (
            dict(server_inputs, EXAM_INGRESS_SUBNET="10.220.0.0/29"),
            dict(server_inputs, EXAM_PROXY_PUBLISH_PORT="21780"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                fixture_exam.settings(ROOT, invalid, server.publish_ip)

    def test_invalid_server_selection_is_rejected(self):
        for inputs in (
            {"FIXTURE_SERVER_IP": "10.135.250.231"},
            {"FIXTURE_SCOPE": "server"},
            {"FIXTURE_SCOPE": "unexpected"},
            *(
                {"FIXTURE_SCOPE": "server", "FIXTURE_SERVER_IP": ip}
                for ip in (
                    "0.0.0.0",
                    "127.0.0.1",
                    "192.168.0.1",
                    "10.0.0.0",
                    "10.255.255.255",
                )
            ),
        ):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                fixture_exam.publication(inputs)


class ControllerTlsTests(unittest.TestCase):
    def test_strict_server_certificate_can_be_renewed_with_same_ca(self):
        with tempfile.TemporaryDirectory(prefix="controller-tls-") as directory:
            state = Path(directory)
            prepare = [
                "bash",
                str(ROOT / "scripts/exam-controller-tls.sh"),
                str(state),
                "--server",
                "10.135.250.231",
            ]
            verify = [
                "openssl",
                "verify",
                "-x509_strict",
                "-purpose",
                "sslserver",
                "-verify_ip",
                "10.135.250.231",
                "-CAfile",
                str(state / "tls/ca.crt"),
                str(state / "tls/server.crt"),
            ]
            subprocess.run(prepare, check=True, capture_output=True)  # nosec B603
            subprocess.run(verify, check=True, capture_output=True)  # nosec B603
            ca = (state / "controller-ca.cer").read_bytes()
            leaf = (state / "tls/server.crt").read_bytes()
            subprocess.run(  # nosec B603 # fixed controller test argv
                [*prepare, "--renew"], check=True, capture_output=True
            )
            subprocess.run(verify, check=True, capture_output=True)  # nosec B603
            self.assertEqual(ca, (state / "controller-ca.cer").read_bytes())
            self.assertNotEqual(leaf, (state / "tls/server.crt").read_bytes())

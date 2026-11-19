"""Штатный Compose config: optional proxy не получает state/dependencies fixture."""

import json
import os
from pathlib import Path
import subprocess  # nosec B404 # fixed Compose config only; no daemon or shell
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


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
        result = self.compose(
            environment,
            files=(
                "compose.yml",
                "deploy/fixture-native.yml",
                "compose.auth.yml",
                "deploy/exam-fixture.yml",
            ),
        )
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

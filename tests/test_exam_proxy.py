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

    def compose(self, environment=None):
        return subprocess.run(  # nosec B603, B607 # fixed config argv and synthetic paths, no daemon
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--profile",
                "exam",
                "-f",
                "deploy/exam-compose.yml",
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

"""Реальный Compose config: steady graph не наследует provisioning fixture."""

import json
import os
from pathlib import Path
import subprocess  # nosec B404 # fixed Compose config argv; no daemon or shell
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeComposeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime-config-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        platform = self.directory / "platform"
        (platform / "compose").mkdir(parents=True)
        # Producer contract ровно image+platform; сам runtime image здесь не запускается.
        (platform / "compose/runtime.yml").write_text(
            "services:\n  community:\n    image: example.invalid/community\n"
            "    platform: linux/amd64\n"
        )
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "DOCKER_CONFIG")
        }
        self.environment.update(
            {
                "PWD": str(ROOT),
                "PLATFORM_ROOT": str(platform),
                "COMPOSE_PROJECT_NAME": "runtime-config-test",
                "DEPLOY_ROOT": str(self.directory / "restored"),
                "PL_IMAGE": "example.invalid/community@sha256:" + "1" * 64,
                "GATEWAY_IMAGE": "example.invalid/gateway@sha256:" + "2" * 64,
                "NGINX_IMAGE": "example.invalid/nginx@sha256:" + "3" * 64,
                "PL_CONFIG_FILE": str(self.directory / "private/pl.json"),
                "BRIDGE_CONFIG_DIR": str(self.directory / "private/bridge"),
                "GATEWAY_CONFIG_DIR": str(self.directory / "private/gateway"),
                "NGINX_CONFIG": str(self.directory / "nginx.conf"),
                "TLS_DIR": str(self.directory / "tls"),
                "PROXY_PRIVATE": str(self.directory / "private/proxy"),
                "PL_CA_FILE": str(self.directory / "public-ca.crt"),
                "PL_TLS_NAME": "pl.example.invalid",
                "STATE_DIR": str(self.directory / "unused-fixture-state"),
            }
        )

    def compose(self, name="compose.runtime.yml", *, environment=None):
        return subprocess.run(  # nosec B603, B607 # fixed installed Compose config; isolated synthetic env
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                name,
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

    def test_runtime_graph_and_mounts_exclude_fixture_provisioning(self):
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        services = config["services"]
        self.assertEqual(set(services), {"pl", "bridge", "gateway", "proxy"})
        self.assertFalse(config.get("volumes"))
        self.assertFalse(services["pl"].get("depends_on"))
        for service, dependency in [
            ("bridge", "pl"),
            ("gateway", "bridge"),
            ("proxy", "gateway"),
        ]:
            self.assertEqual(set(services[service]["depends_on"]), {dependency})
            self.assertEqual(
                services[service]["depends_on"][dependency]["condition"],
                "service_healthy",
            )
        self.assertEqual(services["bridge"]["network_mode"], "service:pl")
        self.assertEqual(services["gateway"]["user"], "1000:1000")
        for service, image_variable in [
            ("pl", "PL_IMAGE"),
            ("bridge", "PL_IMAGE"),
            ("gateway", "GATEWAY_IMAGE"),
            ("proxy", "NGINX_IMAGE"),
        ]:
            self.assertEqual(
                services[service]["image"], self.environment[image_variable]
            )
            self.assertFalse(services[service].get("profiles"))
            for mount in services[service]["volumes"]:
                self.assertEqual(mount["type"], "bind")
                self.assertIs(mount["bind"]["create_host_path"], False)
                self.assertNotIn("unused-fixture-state", mount["source"])
                self.assertNotIn(mount["target"], {"/state", "/run/base-pl"})
        pl_mounts = {m["target"]: m for m in services["pl"]["volumes"]}
        for target, relative in [
            ("/var/postgres", "postgres"),
            ("/files", "files"),
            ("/PrairieLearn/s3rver", "s3rver"),
            ("/course", "courses"),
            ("/jobs", "jobs"),
        ]:
            self.assertEqual(
                pl_mounts[target]["source"],
                str(Path(self.environment["DEPLOY_ROOT"]) / relative),
            )
        self.assertTrue(pl_mounts["/run/pl-native/config.json"]["read_only"])
        pl_command = services["pl"]["command"][0]
        self.assertLess(
            pl_command.index("test -s /var/postgres/PG_VERSION"),
            pl_command.index("exec bash /local/config/start-pl.sh"),
        )
        self.assertEqual(
            set(services["pl"]["environment"]),
            {"NODE_ENV", "HOST_JOBS_DIR", "PL_CONFIG_PATH"},
        )

    def test_proxy_uses_only_own_443_with_preserved_tls_identity(self):
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        proxy = json.loads(result.stdout)["services"]["proxy"]
        self.assertEqual(len(proxy["ports"]), 1)
        self.assertEqual(proxy["ports"][0]["target"], 443)
        self.assertEqual(proxy["ports"][0]["published"], "443")
        self.assertEqual(proxy["ports"][0]["host_ip"], "127.0.0.1")
        self.assertFalse(proxy["networks"]["default"].get("aliases"))
        self.assertEqual(
            proxy["command"],
            ["nginx", "-c", "/etc/nginx/nginx.conf", "-g", "daemon off;"],
        )
        health = proxy["healthcheck"]["test"]
        self.assertIn("pl.example.invalid:443:127.0.0.1:443", health)
        self.assertEqual(health[-1], "https://pl.example.invalid/healthz")
        self.assertIn("--connect-to", health)
        self.assertEqual(health[health.index("--noproxy") + 1], "*")
        self.assertEqual(health[health.index("--cacert") + 1], "/run/pl-tls/ca.crt")
        self.assertNotIn("--insecure", health)
        self.assertNotIn("-k", health)
        proxy_mounts = {m["target"]: m for m in proxy["volumes"]}
        self.assertEqual(
            set(proxy_mounts),
            {
                "/etc/nginx/nginx.conf",
                "/tls",
                "/etc/nginx/private",
                "/run/pl-tls/ca.crt",
            },
        )
        self.assertTrue(all(m["read_only"] for m in proxy_mounts.values()))

    def test_missing_required_runtime_input_is_rejected(self):
        for variable in [
            "COMPOSE_PROJECT_NAME",
            "DEPLOY_ROOT",
            "PL_IMAGE",
            "GATEWAY_IMAGE",
            "NGINX_IMAGE",
            "PL_CONFIG_FILE",
            "BRIDGE_CONFIG_DIR",
            "GATEWAY_CONFIG_DIR",
            "NGINX_CONFIG",
            "TLS_DIR",
            "PROXY_PRIVATE",
            "PL_CA_FILE",
            "PL_TLS_NAME",
        ]:
            with self.subTest(variable=variable):
                environment = dict(self.environment)
                del environment[variable]
                result = self.compose(environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(variable, result.stderr)

    def test_fixture_still_has_original_provisioning_and_ports(self):
        result = self.compose("compose.yml")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        services = config["services"]
        self.assertEqual(
            set(services),
            {
                "pl",
                "config-init",
                "bootstrap-admin",
                "moodle-db",
                "moodle-init",
                "moodle",
                "proxy",
            },
        )
        self.assertEqual(
            {p["target"] for p in services["proxy"]["ports"]}, {19443, 19444}
        )
        self.assertIn("config-init", services["pl"]["depends_on"])
        self.assertIn("moodle", services["proxy"]["depends_on"])

"""Настоящий prepare: private Git, физические bytes, npm ci и отказ публикации."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess  # nosec B404 # explicit test argv in owned temporary directories
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare-course.py"
SPEC = importlib.util.spec_from_file_location("prepare_snapshot", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Не найден prepare module")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)

PROBE = """\
import json, os, subprocess, sys
from pathlib import Path
root = Path.cwd()
selected = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\\0")
assert Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()) == root
assert (root / ".git").is_dir() and not (root / ".git/FOREIGN_ONLY").exists()
assert (root / "tracked.py").read_text() == 'VALUE = "dirty"\\n'
assert (root / "new.py").read_text() == 'VALUE = "untracked"\\n'
assert not (root / "deleted.py").exists()
assert "ignored-tracked.py" in selected and "ignored-new.py" not in selected
assert "wild[0].py" in selected and "wild0.py" not in selected
assert ":literal.py" in selected and "linked.py" in selected and "space name.py" in selected
assert "info-excluded.cjs" not in selected and "repo-excluded.cjs" not in selected
assert (root / "linked.py").is_symlink()
assert (root / "authored.json").read_text() == '{"keep":1}'
assert not (root / "POSTINSTALL_RAN").exists()
subprocess.run(["prettier", "--check", "_quarto.yml", "_quarto-preview.yml"], check=True)
subprocess.run([str(root / "node_modules/.bin/snapshot-dev-cli"), "input.cjs"], check=True)
output = Path(sys.argv[1])
for name in ("native/infoCourse.json", "native/delivery.json", "site/index.html", "native-checks.json", "runtime-profiles.json"):
    path = output / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
(output / "reports").mkdir()
(output / "reports/probe.json").write_text(json.dumps({"selected": selected, "root": str(root), "devCli": True, "nodeEnv": os.environ.get("NODE_ENV")}))
"""


class SnapshotCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="prepare-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.platform = self.root / "platform"
        self.platform.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("git", "go-task", "npm", "node", "prettier", "python3"):
            executable = shutil.which(name)
            if executable is None and name == "prettier":
                executable = str(ROOT / "node_modules/.bin/prettier")
            if executable is None or not Path(executable).is_file():
                self.fail("Для публичной prepare приёмки нужен " + name)
            (self.bin / name).symlink_to(executable)
        # Эти preflight CLI не исполняются: fixture build проверяет Git/npm/Node,
        # а Quarto render не входит в ограниченную приёмку без Docker.
        for name in ("quarto", "pyright", "shellcheck", "tsc"):
            (self.bin / name).symlink_to(shutil.which(name) or sys.executable)
        self.env = dict(
            prepare.git_environment(),
            PATH=str(self.bin),
            PYTHONDONTWRITEBYTECODE="1",
            NODE_ENV="production",
            npm_config_cache=str(self.root / "npm-cache"),
            npm_config_audit="false",
            npm_config_fund="false",
            npm_config_update_notifier="false",
        )
        self.put(
            ".gitignore", "ignored-*.py\nwild*.py\nnode_modules/\nPOSTINSTALL_RAN\n"
        )
        self.put(
            "_quarto.yml",
            "subprojects: []\nwebsite:\n  site-url: https://example.org/course/\n  navbar:\n    left:\n      - href: https://example.org/course/theory/\n",
        )
        self.put(
            "Taskfile.yml",
            'version: "3"\ntasks:\n  build:\n    cmds:\n      - python3 probe.py "{{.BUILD_DIR}}"\n',
        )
        self.put("probe.py", PROBE)
        self.put("authored.json", '{"keep":1}')
        self.put("input.cjs", "const value = 1;\n")
        self.put("tracked.py", 'VALUE = "staged"\n')
        self.put("ignored-tracked.py", "VALUE = 1\n")
        self.put("wild[0].py", "VALUE = 1\n")
        self.put(":literal.py", "VALUE = 1\n")
        self.put("space name.py", "VALUE = 1\n")
        self.put("deleted.py", "VALUE = 1\n")
        self.outside = self.root / "outside.py"
        self.outside.write_text("OUTSIDE_SYNTHETIC = 1\n")
        (self.source / "linked.py").symlink_to(self.outside)
        self.put(
            "dev-package/package.json",
            json.dumps(
                {
                    "name": "snapshot-dev-tool",
                    "version": "1.0.0",
                    "bin": {"snapshot-dev-cli": "cli.cjs"},
                }
            ),
        )
        self.put(
            "dev-package/cli.cjs",
            '#!/usr/bin/env node\nconst fs = require("node:fs");\nconst vm = require("node:vm");\nnew vm.Script(fs.readFileSync(process.argv[2], "utf8"));\n',
        )
        self.put(
            "package.json",
            json.dumps(
                {
                    "name": "snapshot-course-fixture",
                    "version": "1.0.0",
                    "private": True,
                    "devDependencies": {"snapshot-dev-tool": "file:dev-package"},
                    "scripts": {"postinstall": "touch POSTINSTALL_RAN"},
                }
            ),
        )
        # Штатный lock из локальной file: зависимости, без registry/network.
        self.command(
            [
                str(self.bin / "npm"),
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--include=dev",
                "--offline",
            ],
            self.source,
        )
        self.command(
            ["git", "init", "--quiet", "--template=", "--initial-branch=fixture"],
            self.source,
        )
        self.command(["git", "add", "."], self.source)
        self.command(
            [
                "git",
                "--literal-pathspecs",
                "add",
                "--force",
                "ignored-tracked.py",
                "wild[0].py",
            ],
            self.source,
        )
        (self.source / ".git/FOREIGN_ONLY").write_text(
            "do not copy foreign Git metadata\n"
        )
        source_excludes = self.root / "source-excludes"
        source_excludes.write_text("/repo-excluded.cjs\n")
        self.command(
            ["git", "config", "--local", "core.excludesFile", str(source_excludes)],
            self.source,
        )
        (self.source / ".git/info").mkdir(exist_ok=True)
        (self.source / ".git/info/exclude").write_text("/info-excluded.cjs\n")
        self.put("info-excluded.cjs", "INVALID IGNORED JAVASCRIPT\n")
        self.put("repo-excluded.cjs", "INVALID IGNORED JAVASCRIPT\n")
        self.put("tracked.py", 'VALUE = "dirty"\n')
        self.put("new.py", 'VALUE = "untracked"\n')
        self.put("ignored-new.py", "INVALID IGNORED FILE\n")
        self.put("wild0.py", "INVALID IGNORED FILE\n")
        (self.source / "deleted.py").unlink()
        self.output = self.root / "builds"

    def put(self, name, content):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def command(self, command, cwd, *, env=None, check=True):
        return subprocess.run(
            command,
            cwd=cwd,
            env=env or self.env,
            check=check,
            capture_output=True,
            text=True,
            timeout=45,
        )  # nosec B603 # explicit test CLI, no shell

    def invoke(self, *, env=None):
        return self.command(
            [
                sys.executable,
                str(SCRIPT),
                "--course",
                "java",
                "--source",
                str(self.source),
                "--platform",
                str(self.platform),
                "--output-root",
                str(self.output),
                "--site-url",
                "http://127.0.0.1:8088/",
            ],
            ROOT,
            env=env,
            check=False,
        )

    def old_release(self):
        course = self.output / "java"
        course.mkdir(parents=True, exist_ok=True)
        result = (
            self.output.parent
            / (self.output.name + "-work")
            / "java/.prepare-accepted/result"
        )
        for name in (
            "native/infoCourse.json",
            "native/delivery.json",
            "site/index.html",
            "native-checks.json",
            "runtime-profiles.json",
        ):
            path = result / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("accepted")
        prepare.publish(result, course)
        (course / "active").symlink_to("releases/accepted")
        (course / "site").symlink_to("active/site")
        return course

    def assert_preserved(self, result, diagnostic):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(diagnostic, result.stdout + result.stderr)
        course = self.output / "java"
        self.assertEqual((course / "prepared").readlink(), Path("releases/accepted"))
        self.assertEqual((course / "active").readlink(), Path("releases/accepted"))
        self.assertEqual((course / "site/index.html").read_text(), "accepted")

    def test_real_prepare_uses_private_git_and_dev_cli_with_dirty_untracked_bytes(self):
        index = (self.source / ".git/index").read_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(
            (self.output / "java/prepared/reports/probe.json").read_text()
        )
        self.assertTrue(report["devCli"])
        self.assertEqual(report["nodeEnv"], "production")
        self.assertEqual((self.source / ".git/index").read_bytes(), index)
        self.assertEqual((self.source / "tracked.py").read_text(), 'VALUE = "dirty"\n')
        self.assertFalse((self.source / "node_modules").exists())
        self.assertNotEqual(Path(report["root"]), self.source)

    def test_real_node_syntax_failure_keeps_prepared_and_active(self):
        self.old_release()
        self.put("input.cjs", "const value = ;\n")
        self.assert_preserved(self.invoke(), "SyntaxError")

    def test_npm_failure_missing_lock_and_missing_cli_keep_previous_release(self):
        npm = (self.bin / "npm").resolve()
        for failure in (
            "bad-lock",
            "missing-lock",
            "missing-npm",
            "missing-prettier",
            "bad-formatter-config",
            "missing-project-cli",
        ):
            with self.subTest(failure=failure):
                if not (self.output / "java/prepared").exists():
                    self.old_release()
                lock = self.source / "package-lock.json"
                original = lock.read_bytes()
                env = dict(self.env)
                if failure == "bad-lock":
                    lock.write_text("invalid npm lock")
                elif failure == "missing-lock":
                    lock.unlink()
                elif failure == "missing-npm":
                    (self.bin / "npm").unlink()
                elif failure == "missing-prettier":
                    formatter = (self.bin / "prettier").resolve()
                    (self.bin / "prettier").unlink()
                elif failure == "bad-formatter-config":
                    self.put(".prettierrc.json", "invalid prettier config")
                else:
                    self.put(
                        "probe.py",
                        PROBE.replace(
                            "snapshot-dev-cli", "missing-cli-negative-control"
                        ),
                    )
                result = self.invoke(env=env)
                self.assert_preserved(
                    result,
                    {
                        "bad-lock": "npm",
                        "missing-lock": "package-lock.json",
                        "missing-npm": "npm",
                        "missing-prettier": "prettier",
                        "bad-formatter-config": ".prettierrc.json",
                        "missing-project-cli": "missing-cli-negative-control",
                    }[failure],
                )
                lock.write_bytes(original)
                if failure == "missing-npm":
                    (self.bin / "npm").symlink_to(npm)
                if failure == "missing-prettier":
                    (self.bin / "prettier").symlink_to(formatter)
                if failure == "bad-formatter-config":
                    (self.source / ".prettierrc.json").unlink()
                self.put("probe.py", PROBE)

    def test_git_selection_environment_cannot_write_foreign_index(self):
        victim = self.root / "victim"
        victim.mkdir()
        self.command(["git", "init", "--quiet", "--template="], victim)
        foreign_index = victim / "foreign-index"
        foreign_index.write_text("SYNTHETIC FOREIGN INDEX\n")
        source_index = (self.source / ".git/index").read_bytes()
        injected = dict(
            self.env,
            GIT_DIR=str(victim / ".git"),
            GIT_WORK_TREE=str(victim),
            GIT_INDEX_FILE=str(foreign_index),
            GIT_COMMON_DIR=str(victim / ".git"),
            GIT_OBJECT_DIRECTORY=str(victim / "objects"),
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="core.worktree",
            GIT_CONFIG_VALUE_0=str(victim),
        )
        result = self.invoke(env=injected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(foreign_index.read_text(), "SYNTHETIC FOREIGN INDEX\n")
        self.assertEqual((self.source / ".git/index").read_bytes(), source_index)
        self.assertFalse((victim / "objects").exists())

    def test_linked_worktree_and_non_git_source_remain_supported(self):
        self.command(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            self.source,
        )
        worktree = self.root / "linked-source"
        self.command(
            ["git", "worktree", "add", "--quiet", "--detach", str(worktree)],
            self.source,
        )
        for name in ("tracked.py", "new.py"):
            shutil.copyfile(self.source / name, worktree / name)
        (worktree / "deleted.py").unlink()
        self.source = worktree
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shutil.rmtree(self.source / ".git") if (self.source / ".git").is_dir() else (
            self.source / ".git"
        ).unlink()
        # No-Git source has no tracked-ignored semantics; don't demand those in its probe.
        self.put(
            "probe.py",
            PROBE.replace(
                'assert "ignored-tracked.py" in selected and "ignored-new.py" not in selected',
                'assert "ignored-tracked.py" not in selected and "ignored-new.py" not in selected',
            ).replace(
                'assert "wild[0].py" in selected and "wild0.py" not in selected',
                'assert "wild[0].py" not in selected and "wild0.py" not in selected',
            ),
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ignored_output_parent_does_not_supply_snapshot_git_context(self):
        parent = self.root / "output-parent"
        parent.mkdir()
        self.command(["git", "init", "--quiet", "--template="], parent)
        (parent / ".gitignore").write_text("state/\nstate-work/\n")
        self.output = parent / "state"
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(
            (self.output / "java/prepared/reports/probe.json").read_text()
        )
        self.assertTrue(Path(report["root"]).is_relative_to(parent / "state-work"))

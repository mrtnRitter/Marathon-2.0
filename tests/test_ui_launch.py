"""UI commands must not go through .cmd wrappers, even with spaces and & in the repo path."""

from pathlib import Path
from unittest.mock import Mock

import pytest

import build
import run as runner


@pytest.fixture()
def ui_setup(tmp_path: Path, monkeypatch):
    root = tmp_path / 'OneDrive - LOOKS Film & TV Produktionen GmbH' / 'Marathon 2.0'
    node_dir = root / '.tools' / 'node'
    modules_dir = root / 'ui' / 'node_modules'
    static_dir = root / 'marathon' / 'server' / 'static'
    node_dir.mkdir(parents=True)
    static_dir.mkdir(parents=True)
    (node_dir / 'node.exe').write_text('node fixture', encoding='utf-8')
    for path in build.ui_cli_paths.values():
        target = modules_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('cli fixture', encoding='utf-8')
    (static_dir / 'index.html').write_text('ui fixture', encoding='utf-8')
    monkeypatch.setattr(build, 'is_windows', True)
    monkeypatch.setattr(build, 'node_dir', node_dir)
    monkeypatch.setattr(build, 'ui_modules_dir', modules_dir)
    monkeypatch.setattr(build, 'ui_dir', root / 'ui')
    monkeypatch.setattr(build, 'static_dir', static_dir)
    monkeypatch.setattr(runner, 'ui_dir', root / 'ui')
    monkeypatch.setattr(runner, 'ui_modules_dir', modules_dir)
    return root


def test_typescript_command_keeps_complete_paths(ui_setup):
    assert build.ui_command('typescript', '-p', 'tsconfig.json') == [
        str(ui_setup / '.tools/node/node.exe'),
        str(ui_setup / 'ui/node_modules/typescript/bin/tsc'),
        '-p',
        'tsconfig.json',
    ]


def test_build_runs_typescript_then_vite_directly(ui_setup, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(build, 'run', execute)
    build.build_ui()
    commands = [call.args[0] for call in execute.call_args_list]
    assert commands == [build.ui_command('typescript', '-p', 'tsconfig.json'), build.ui_command('vite', 'build')]
    assert all(not any(arg.endswith('.cmd') or 'npm-cli' in arg for arg in command) for command in commands)
    assert all(call.kwargs['cwd'] == ui_setup / 'ui' for call in execute.call_args_list)


def test_execution_does_not_enable_shell(ui_setup, monkeypatch):
    execute = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(build.subprocess, 'run', execute)
    command = build.ui_command('vite', 'build')
    build.run(command, cwd=ui_setup / 'ui', env=build.node_env())
    assert execute.call_args.args[0] == command
    assert execute.call_args.kwargs['shell'] is False


def test_live_preview_runs_vite_directly(ui_setup, monkeypatch):
    execute = Mock(return_value=0)
    monkeypatch.setattr(runner.subprocess, 'call', execute)
    assert runner.start_dev() == 0
    assert execute.call_args.args[0] == build.ui_command('vite')
    assert execute.call_args.kwargs['cwd'] == ui_setup / 'ui'
    assert execute.call_args.kwargs['shell'] is False


def test_missing_cli_has_actionable_error(ui_setup):
    (build.ui_modules_dir / build.ui_cli_paths['vite']).unlink()
    with pytest.raises(build.BuildError, match='Zuerst python build.py'):
        build.ui_command('vite')


def test_typescript_failure_prevents_vite(ui_setup, monkeypatch):
    execute = Mock(side_effect=build.BuildError('TypeScript fehlgeschlagen'))
    monkeypatch.setattr(build, 'run', execute)
    with pytest.raises(build.BuildError, match='TypeScript fehlgeschlagen'):
        build.build_ui()
    execute.assert_called_once()


def test_cli_paths_match_current_package_scripts():
    import json
    package = json.loads(build.ui_package_file.read_text(encoding='utf-8'))
    assert package['scripts']['build'] == 'tsc -p tsconfig.json && vite build'
    assert package['scripts']['dev'] == 'vite'
    assert build.ui_cli_paths == {
        'typescript': Path('typescript/bin/tsc'),
        'vite': Path('vite/bin/vite.js'),
    }

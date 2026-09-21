"""Explicit handoff from evidence gathering to approved workspace analysis."""
from pathlib import PurePosixPath

NAME = 'begin_research_analysis'
TOOLS = {'execute_python', 'execute_node', 'run_shell', 'read_file', 'write_file', 'edit_file',
         'list_dir', 'glob_search', 'grep_search'}


def capabilities():
    from app.config import get_sandbox_config
    config = get_sandbox_config()
    runner = config.get('runner', 'local')
    if runner == 'container':
        network = config.get('container', {}).get('network', 'none')
        description = ('container networking is disabled' if network == 'none' else
                       f'container network mode is {network}; external access is configured but not connectivity-tested')
    elif runner == 'local':
        description = 'execution uses host networking; connectivity has not been tested'
    else:
        description = 'network access depends on the configured runner; connectivity has not been tested'
    return f'Sandbox runner: {runner}; {description}.'


def valid_path(value):
    path = PurePosixPath(value)
    return bool(value.strip()) and not path.is_absolute() and '..' not in path.parts and '\\' not in value


def remember_files(ctx, artifacts):
    """Keep a bounded inventory of this turn's tool-published paths, including across approval."""
    paths = ctx.research.state.setdefault('generated_files', [])
    for artifact in artifacts:
        path = artifact.get('path', '')
        if len(path) <= 300 and valid_path(path):
            if path in paths:
                paths.remove(path)
            paths.append(path)
    del paths[:-64]


def available_files(ctx):
    from app.workspace.manager import resolve_in_workspace
    paths = []
    for path in ctx.research.state.get('generated_files', []):
        try:
            target = resolve_in_workspace(ctx.conversation_id, path)
            if target.is_file() and target.stat().st_size > 0:
                paths.append(path)
        except (ValueError, OSError):
            pass
    return paths


def deliverables(ctx):
    """Check actual workspace files, not a model's completion claim. Never read their content."""
    from app.workspace.manager import resolve_in_workspace
    results = []
    for path in ctx.research.state.get('analysis_outputs', []):
        try:
            target = resolve_in_workspace(ctx.conversation_id, path)
            present = target.is_file() and target.stat().st_size > 0
        except (ValueError, OSError):
            present = False
        results.append({'path': path, 'nonempty_file_exists': present})
    ctx.research.state['analysis_delivery'] = results
    existing = list(dict.fromkeys(available_files(ctx) + [r['path'] for r in results if r['nonempty_file_exists']]))
    missing = [r['path'] for r in results if not r['nonempty_file_exists']]
    ctx.research.state['delivery'] = {'available_files': existing, 'missing_files': missing,
        'status': ('partial' if existing else 'missing') if missing else ('present' if results else 'not_declared')}
    return results

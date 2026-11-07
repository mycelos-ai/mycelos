"""Versioned local import recipes and bounded, model-free inspection.

Glob rules match relative POSIX paths, case-insensitively. ``**`` spans zero
or more directories. Exclusions win. Supported document extensions and internal
metadata exclusions always apply. Inspection counts describe candidate units;
empty documents and invalid transcripts can change actual import counts.
"""
from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatchcase
from functools import lru_cache
import os
from pathlib import Path

KINDS = ('file', 'folder', 'git', 'mercurial', 'codex', 'claude', 'obsidian')
EXCLUDED = {'.git', '.hg', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__',
            'dist', 'build', 'target', '.next', '.worktrees', 'worktrees'}
MAX_FILE_BYTES = 64 * 1024 * 1024
INSPECTION_ENTRIES = 10_000
SAMPLE_FILES = 5
SAMPLE_BYTES = 4096
_DESCRIPTIONS = {
    'obsidian': 'Notizen einzeln lesen. Eigenschaften, vorhandene Links und Verweise auf Anhänge erhalten.',
    'file': 'Eine Textdatei oder PDF-Datei als Dokument übernehmen.',
    'folder': 'Dokumente lesen. README und Dokumentation enthaltener Projekte als Projekt zusammenfassen.',
    'git': 'README, Architektur und Dokumentation des Git-Projekts zusammenfassen.',
    'mercurial': 'README, Architektur und Dokumentation des Mercurial-Projekts zusammenfassen.',
    'codex': 'Abgeschlossene Nachrichten und Werkzeugergebnisse aus Codex-Gesprächen lesen.',
    'claude': 'Nachrichten des aktuellen Gesprächszweigs und Werkzeugergebnisse aus Claude lesen.',
}

_STEPS = [
    {'id': 'identify', 'description': 'Quellenart und enthaltene Projekte feststellen.', 'uses_model': False},
    {'id': 'filter', 'description': 'Dateien nach den gespeicherten Regeln auswählen.', 'uses_model': False},
    {'id': 'extract', 'description': 'Dokumente oder abgeschlossene Gesprächsnachrichten lesen und als Einheiten zusammenfassen.', 'uses_model': False},
    {'id': 'checksum', 'description': 'Inhalt mit dem letzten Import vergleichen. Unveränderte Einheiten überspringen.', 'uses_model': False},
    {'id': 'summarize', 'description': 'Nur neue oder geänderte Einheiten im Analysemodus mit dem Modell bearbeiten. Modus, Zweck und Modell stammen aus der Quelle.', 'uses_model': True},
    {'id': 'save', 'description': 'Wissen und Herkunft für spätere Importe speichern.', 'uses_model': False},
]


def templates() -> list[dict]:
    return [dict(template_id=f'{kind}-v1', version=1, kind=kind,
                 description=_DESCRIPTIONS[kind], steps=deepcopy(_STEPS),
                 include=['**/*.jsonl'] if kind in {'codex', 'claude'} else ['**/*.md', '**/*.txt', '**/*.pdf'],
                 exclude=['Templates/**', 'Vorlagen/**'] if kind == 'obsidian' else [], project_documents_only=kind in {'folder', 'git', 'mercurial'})
            for kind in KINDS]


def resolve_workflow(kind: str, root: Path, workflow=None) -> dict:
    if kind not in KINDS:
        raise ValueError('Unbekannte Quellenart')
    base = next(item for item in templates() if item['kind'] == kind)
    if workflow is None:
        return base
    if not isinstance(workflow, dict) or set(workflow) - set(base):
        raise ValueError('Ungültiger Quellenworkflow')
    result = {**base, **deepcopy(workflow)}
    if result['template_id'] != base['template_id'] or type(result['version']) is not int or result['version'] != 1:
        raise ValueError('Unbekannte Workflow-Vorlage oder Version')
    if result['steps'] != base['steps']:
        raise ValueError('Die festen Schritte der Workflow-Vorlage dürfen sich nicht ändern')
    if result['kind'] != kind or type(result['project_documents_only']) is not bool:
        raise ValueError('Workflow passt nicht zur Quellenart')
    if not isinstance(result['description'], str) or len(result['description']) > 2000:
        raise ValueError('Ungültige Workflow-Beschreibung')
    for field in ('include', 'exclude'):
        rules = result[field]
        if not isinstance(rules, list) or len(rules) > 100:
            raise ValueError('Dateiregeln benötigen eine Liste mit höchstens 100 Mustern')
        for rule in rules:
            if (not isinstance(rule, str) or not rule.strip() or len(rule) > 256
                    or rule.startswith(('/', '~')) or '\\' in rule or ':' in rule
                    or any(ord(char) < 32 for char in rule)
                    or any(part in {'..', '.'} for part in rule.split('/'))):
                raise ValueError('Dateiregeln benötigen relative Pfadmuster ohne ..')
    return result


def source_root(kind: str, root: Path) -> Path:
    root = Path(root).expanduser()
    if root.is_symlink():
        raise ValueError('Verknüpfungen sind keine zulässige Quelle')
    root = root.resolve()
    if kind == 'file':
        if not root.is_file():
            raise ValueError('Die Quelldatei ist nicht verfügbar')
    elif not root.is_dir():
        raise ValueError('Der Quellenordner ist nicht verfügbar')
    if kind in {'git', 'mercurial'}:
        marker = root / ('.git' if kind == 'git' else '.hg')
        if marker.is_symlink() or not marker.exists():
            raise ValueError('Der Projektordner enthält keinen passenden Git- oder Mercurial-Marker')
    return root


def _match(path: str, pattern: str) -> bool:
    parts, rules = path.lower().split('/'), pattern.lower().rstrip('/').split('/')
    @lru_cache(maxsize=None)
    def match(i, j):
        if j == len(rules):
            return i == len(parts)
        if rules[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatchcase(parts[i], rules[j]) and match(i + 1, j + 1)
    return match(0, 0)


def project_for(path: Path, root: Path, kind: str) -> Path | None:
    if kind in {'git', 'mercurial'}:
        return root
    if kind == 'folder':
        return next((parent for parent in path.parents if parent.is_relative_to(root)
                     and any((parent / marker).exists() for marker in ('.git', '.hg'))), None)
    return None


def selected_paths(kind: str, root: Path, workflow: dict, state: dict | None = None, extensions=None, check_size=True):
    """Yield the same file/project pairs for both preview and import.

    A state argument bounds total directory entries for inspection. Import has
    no entry cap and therefore does not silently drop later source files.
    """
    if extensions is None:
        extensions = {'.jsonl'} if kind in {'codex', 'claude'} else {'.md', '.txt', '.pdf'}
    excluded = ({'.git', '.hg', '.obsidian', '.trash'} if kind == 'obsidian' else EXCLUDED) | ({'subagents'} if kind in {'codex', 'claude'} else set())
    def candidates():
        if kind == 'file':
            yield root
            return
        pending = [root]
        while pending:
            directory = pending.pop()
            entries = []
            with os.scandir(directory) as stream:
                for entry in stream:
                    if state is not None:
                        if state['entries'] >= INSPECTION_ENTRIES:
                            state['truncated'] = True
                            break
                        state['entries'] += 1
                    entries.append(entry)
            children = []
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.is_symlink() or entry.name in excluded:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    children.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            # Match the original os.walk reader: sorted files, then sorted
            # child directories in depth-first order. Stable order preserves
            # project content fingerprints across upgrades.
            pending.extend(reversed(children))
            if state is not None and state['truncated']:
                return
    for path in candidates():
        if path.suffix.lower() not in extensions:
            continue
        relative = path.name if kind == 'file' else path.relative_to(root).as_posix()
        if not any(_match(relative, rule) for rule in workflow['include']):
            continue
        if any(_match(relative, rule) for rule in workflow['exclude']):
            continue
        project = None if kind == 'obsidian' else project_for(path, root, kind)
        if project and workflow['project_documents_only']:
            rel = path.relative_to(project)
            if not (path.name.lower().startswith(('readme', 'architecture', 'specification'))
                    or rel.parts[0].lower() in {'docs', 'specs', 'specifications'}):
                continue
        if check_size and path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f'Datei zu groß: {path}')
        yield path, project


def inspect_source(kind: str, root: Path, workflow=None) -> dict:
    workflow = resolve_workflow(kind, root, workflow)
    root = source_root(kind, root)
    state = {'entries': 0, 'truncated': False}
    paths = list(selected_paths(kind, root, workflow, state))
    samples = []
    for path, project in paths[:SAMPLE_FILES]:
        size = path.stat().st_size
        excerpt = ''
        # PDF parsing and transcript reconstruction belong to import. Do not
        # expose hidden model records through raw JSONL previews.
        if path.suffix.lower() not in {'.pdf', '.jsonl'}:
            with path.open('rb') as stream:
                excerpt = stream.read(SAMPLE_BYTES).decode('utf-8', errors='replace')
        samples.append(dict(path=str(path), title=project.name if project else path.stem,
                            bytes=size, excerpt=excerpt, files=[str(path)],
                            project=project.name if project else ''))
    projects = {project for _, project in paths if project}
    units = len(projects) + sum(project is None for _, project in paths)
    scenario = ('project_collection' if kind == 'folder' and projects
                else 'repository' if kind in {'git', 'mercurial'} else kind)
    return dict(workflow=workflow, scenario=scenario, files=len(paths), units=units,
                samples=samples, excerpt='\n\n'.join(s['excerpt'] for s in samples),
                analysis_performed=False, counts_estimated=True, inventory_truncated=state['truncated'],
                truncated=state['truncated'] or len(paths) > SAMPLE_FILES
                or any(s['bytes'] > SAMPLE_BYTES or not s['excerpt'] for s in samples),
                limits=dict(entries=INSPECTION_ENTRIES, sample_files=SAMPLE_FILES, sample_bytes=SAMPLE_BYTES))

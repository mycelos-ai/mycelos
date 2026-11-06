from pathlib import Path

import pytest

from mycelos.knowledge.local_readers import read_units


def api():
    from mycelos.knowledge import source_workflows
    return source_workflows


def test_templates_are_independent_versioned_copies(tmp_path):
    mod = api()
    first = mod.templates()
    assert {x['kind'] for x in first} == {'file', 'folder', 'git', 'mercurial', 'codex', 'claude', 'obsidian'}
    first[0]['include'].clear()
    assert mod.templates()[0]['include']
    assert mod.resolve_workflow('folder', tmp_path)['version'] == 1


@pytest.mark.parametrize('rules', [
    {'include': '../*'}, {'include': ['../*']}, {'exclude': ['/private/*']},
    {'include': ['a/../../b']}, {'include': ['']}, {'version': 2},
    {'project_documents_only': 'false'}, {'unknown': True},
    {'template_id': 'missing'}, {'kind': 'git'},
])
def test_invalid_rules_are_rejected(tmp_path, rules):
    with pytest.raises(ValueError):
        api().resolve_workflow('folder', tmp_path, rules)


def test_file_filters_apply_to_inspect_and_import(tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('Content')
    assert list(read_units('file', path))[0].content == 'Content'
    rules = {'exclude': ['note.txt']}
    assert list(read_units('file', path, rules)) == []
    assert api().inspect_source('file', path, rules)['files'] == 0


@pytest.mark.parametrize('kind,marker', [('git', '.git'), ('mercurial', '.hg')])
def test_repository_defaults_and_filters(tmp_path, kind, marker):
    (tmp_path / marker).mkdir()
    (tmp_path / marker / 'secret.md').write_text('Secret')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'guide.md').write_text('Guide')
    (tmp_path / 'README.md').write_text('Read me')
    (tmp_path / 'fixture.md').write_text('Fixture')
    rules = {'exclude': ['docs/**']}
    result = api().inspect_source(kind, tmp_path, rules)
    units = list(read_units(kind, tmp_path, rules))
    assert result['files'] == 1
    assert len(units) == result['units'] == 1
    assert 'Read me' in units[0].content
    assert 'Guide' not in units[0].content
    assert 'Secret' not in units[0].content
    assert 'Fixture' not in units[0].content


def test_folder_groups_nested_mercurial_project(tmp_path):
    project = tmp_path / 'project'
    (project / '.hg').mkdir(parents=True)
    (project / 'README.md').write_text('Project')
    (project / 'fixture.md').write_text('Fixture')
    (tmp_path / 'note.txt').write_text('Note')
    units = list(read_units('folder', tmp_path))
    assert len(units) == 2
    assert units[1].external_id == 'project:project'
    assert 'Fixture' not in units[1].content
    assert api().inspect_source('folder', tmp_path)['scenario'] == 'project_collection'


def test_inspection_reads_only_bounded_samples(tmp_path, monkeypatch):
    mod = api()
    for i in range(20):
        (tmp_path / f'{i:02}.md').write_text('x' * 20000)
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: pytest.fail('Unbounded read'))
    result = mod.inspect_source('folder', tmp_path)
    assert result['files'] == 20
    assert result['analysis_performed'] is False
    assert len(result['samples']) <= result['limits']['sample_files']
    assert all(len(s['excerpt']) <= result['limits']['sample_bytes'] for s in result['samples'])
    assert result['truncated'] is True


def test_symlink_roots_and_metadata_are_rejected(tmp_path):
    root = tmp_path / 'actual'
    root.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(root)
    with pytest.raises(ValueError):
        api().inspect_source('folder', link)
    (root / '.hg').mkdir()
    (root / '.hg' / 'secret.md').write_text('Secret')
    assert api().inspect_source('folder', root)['files'] == 0


@pytest.mark.parametrize('kind', ['git', 'mercurial'])
def test_explicit_repository_needs_its_marker(tmp_path, kind):
    with pytest.raises(ValueError):
        api().inspect_source(kind, tmp_path)


def test_missing_root_does_not_prevent_workflow_resolution(tmp_path):
    assert api().resolve_workflow('folder', tmp_path / 'gone')['kind'] == 'folder'


def test_inspection_caps_directory_entries(tmp_path, monkeypatch):
    mod = api()
    monkeypatch.setattr(mod, 'INSPECTION_ENTRIES', 3)
    for i in range(10):
        (tmp_path / f'{i}.md').write_text('Document')
    result = mod.inspect_source('folder', tmp_path)
    assert result['files'] <= 3
    assert result['truncated'] is True
    assert len(list(read_units('folder', tmp_path))) == 10


def test_include_glob_respects_directory_boundaries(tmp_path):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'root.md').write_text('Root')
    (tmp_path / 'nested' / 'child.md').write_text('Child')
    assert len(list(read_units('folder', tmp_path, {'include': ['*.md']}))) == 1
    assert len(list(read_units('folder', tmp_path, {'include': ['**/*.md']}))) == 2


def test_reference_steps_explain_model_boundary_and_cannot_change(tmp_path):
    mod = api()
    workflow = mod.resolve_workflow('folder', tmp_path)
    assert [step['id'] for step in workflow['steps']] == ['identify', 'filter', 'extract', 'checksum', 'summarize', 'save']
    assert [step['id'] for step in workflow['steps'] if step['uses_model']] == ['summarize']
    workflow['steps'][0]['description'] = 'Changed'
    with pytest.raises(ValueError):
        mod.resolve_workflow('folder', tmp_path, workflow)
    assert mod.templates()[1]['steps'][0]['description'] != 'Changed'


def test_content_sampling_does_not_mark_inventory_incomplete(tmp_path):
    (tmp_path / 'note.md').write_text('x' * 10000)
    result = api().inspect_source('folder', tmp_path)
    assert result['truncated'] is True
    assert result['inventory_truncated'] is False


def test_project_content_keeps_legacy_directory_order(tmp_path):
    from mycelos.knowledge.local_readers import _paths
    (tmp_path / '.git').mkdir()
    for folder in ('docs', 'docs/nested', 'specs'):
        (tmp_path / folder).mkdir()
    for name in ('README.md', 'docs/a.md', 'docs/z.md', 'docs/nested/n.md', 'specs/b.md'):
        (tmp_path / name).write_text(name)
    old_paths = list(_paths(tmp_path, {'.md', '.txt', '.pdf'}))
    expected = '\n\n'.join(f'## {path.relative_to(tmp_path)}\n{path.read_text()}' for path in old_paths)
    unit = list(read_units('folder', tmp_path))[0]
    assert unit.content == expected
    assert unit.files == tuple(str(path) for path in old_paths)

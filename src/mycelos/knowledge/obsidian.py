"""Read-only Obsidian import. Explicit links never need a model."""
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
import yaml

from .local_readers import SourceUnit, _document, MAX_UNIT_CHARS
from .source_workflows import selected_paths, _match
from mycelos.i18n import t as _t


def properties(text):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if not match:
        return {}, text
    if len(match[1]) > 64000:
        raise ValueError(_t("sources.obsidian_props_too_large"))
    try:
        data = yaml.safe_load(match[1]) or {}
        if not isinstance(data, dict):
            raise ValueError(_t("sources.obsidian_props_shape"))
        data = json.loads(json.dumps(data, default=str, ensure_ascii=False))
    except (yaml.YAMLError, TypeError, RecursionError, ValueError) as error:
        raise ValueError(_t("sources.obsidian_props_invalid")) from error
    return data, text[match.end():]


def resolve(root, current, raw, selected, workflow, attachments=()):
    value = unquote(raw.strip().strip('<>'))
    target, _, anchor = value.partition('#')
    result = {'reference': raw, 'anchor': anchor, 'target': None}
    if urlsplit(target).scheme or target.startswith('//'):
        return {**result, 'state': 'external'}
    if not target:
        return {**result, 'target': current, 'state': 'resolved'}
    if target.startswith('/'):
        return {**result, 'state': 'outside'}
    names = [target] if target.endswith('.md') else [target, target + '.md']
    candidates = []
    for name in names:
        for prefix in (Path(current).parent, Path()):
            path = root / prefix / name
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                continue
            try:
                relative = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            candidates.append(relative)
    if not candidates:
        return {**result, 'state': 'outside'}
    def allowed(relative):
        parts = Path(relative).parts
        return (not any(p in {'.obsidian', '.trash', '.git', '.hg'} for p in parts)
                and not any((root / Path(*parts[:i+1])).is_symlink() for i in range(len(parts)))
                and not any(_match(relative, rule) for rule in workflow['exclude']))
    for candidate in dict.fromkeys(candidates):
        if not allowed(candidate):
            continue
        if candidate in selected:
            return {**result, 'target': candidate, 'state': 'resolved'}
        if (root / candidate).is_file():
            state = 'excluded' if Path(candidate).suffix.lower() in {'.md', '.txt', '.pdf'} else 'attachment'
            return {**result, 'target': candidate, 'state': state}
    # Obsidian's shortest-path links may name a file in another directory.
    matches = [p for p in set(selected) | set(attachments) if any(p == name or p.endswith('/' + name) for name in names) and allowed(p)]
    if len(matches) == 1:
        return {**result, 'target': matches[0], 'state': 'resolved' if matches[0] in selected else 'attachment'}
    return {**result, 'state': 'ambiguous' if len(matches) > 1 else 'missing'}


def links(root, current, text, selected, workflow, attachments=()):
    text = re.sub(r'%%.*?%%', '', text, flags=re.S)
    text = re.sub(r'(?ms)^([`~]{3,})[^\n]*\n.*?^\1[ \t]*$', '', text)
    text = re.sub(r'`+[^`\n]*`+', '', text)
    result = []
    pattern = r'(!?)\[\[([^\]\n]+)\]\]|(!?)\[[^\]\n]*\]\(([^)\n]+)\)'
    for match in re.finditer(pattern, text):
        wiki = match[2] is not None
        raw = match[2].split('|', 1)[0] if wiki else re.split(r'\s+["\']', match[4], maxsplit=1)[0]
        entry = resolve(root, current, raw, selected, workflow, attachments)
        entry['kind'] = 'embed' if (match[1] if wiki else match[3]) else 'link'
        result.append(entry)
    return result


def read_vault(root, workflow):
    paths = [path for path, _ in selected_paths('obsidian', root, workflow)]
    selected = {path.relative_to(root).as_posix() for path in paths}
    media = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.mp3', '.wav', '.m4a', '.mp4', '.webm', '.ogg', '.mov', '.canvas', '.base'}
    attachments = {path.relative_to(root).as_posix() for path, _ in selected_paths(
        'obsidian', root, {**workflow, 'include': ['**/*']}, extensions=media, check_size=False)}
    for path in paths:
        text = _document(path)
        if not text.strip():
            continue
        if len(text) > MAX_UNIT_CHARS:
            raise ValueError(_t("sources.document_too_large", name=path.name))
        relative = path.relative_to(root).as_posix()
        props, _ = properties(text) if path.suffix.lower() == '.md' else ({}, text)
        title = props.get('title') if isinstance(props.get('title'), str) else path.stem
        metadata = {'path': relative, 'properties': props,
                    'links': links(root, relative, text, selected, workflow, attachments) if path.suffix.lower() == '.md' else []}
        yield SourceUnit(relative, title or path.stem, text, (str(path),), metadata=metadata)


def persist_metadata(db, source_id, unit):
    if unit.metadata:
        db.execute('INSERT INTO local_source_metadata(item_id,metadata) '
                   'SELECT id,? FROM local_source_items WHERE source_id=? AND external_id=? '
                   'ON CONFLICT(item_id) DO UPDATE SET metadata=excluded.metadata',
                   (json.dumps(unit.metadata, ensure_ascii=False), source_id, unit.external_id))


def materialize_links(app, source_id):
    rows = app.storage.fetchall('SELECT i.external_id,v.note_id,m.metadata,n.content_hash FROM local_source_items i '
        'JOIN local_source_versions v ON v.id=i.version_id JOIN local_source_metadata m ON m.item_id=i.id '
        "JOIN knowledge_notes n ON n.id=v.note_id WHERE i.source_id=? AND i.available=1 AND n.status='active'", (source_id,))
    notes = {row['external_id']: row for row in rows}
    owner = 'obsidian:' + source_id
    count = 0
    with app.storage.transaction():
        app.storage.execute('DELETE FROM knowledge_relations WHERE created_by=?', (owner,))
        for row in rows:
            for link in json.loads(row['metadata'])['links']:
                target = notes.get(link['target']) if link['state'] == 'resolved' else None
                if not target or row['note_id'] == target['note_id']:
                    continue
                cursor = app.storage.execute('INSERT OR IGNORE INTO knowledge_relations '
                    '(from_id,to_id,kind,reason,from_hash,to_hash,created_by) VALUES(?,?,?,?,?,?,?)',
                    (row['note_id'], target['note_id'], 'related', 'Obsidian-Verweis: ' + link['reference'],
                     row['content_hash'], target['content_hash'], owner))
                count += cursor.rowcount
    return count

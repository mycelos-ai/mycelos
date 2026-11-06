import json
from types import SimpleNamespace
import pytest
from mycelos.knowledge.local_readers import SourceUnit
from mycelos.knowledge.source_analysis import extract_articles


def test_sections_produce_separate_articles_with_exact_evidence():
    calls=[]
    def complete(messages, model, purpose=None):
        text=json.loads(messages[-1]['content'])['text']
        calls.append(text)
        quote='TAIL' if 'TAIL' in text else 'START'
        return SimpleNamespace(content=json.dumps({'articles':[{'title':quote,'content':'Finding '+quote,'quotes':[quote]}]}))
    unit=SourceUnit('x','Doc','START'+'x'*24000+'TAIL',('doc.md',))
    result=extract_articles(SimpleNamespace(llm=SimpleNamespace(complete=complete)),unit,{'model':'custom/fast','purpose':''})
    assert len(result['articles'])==2
    assert len(calls)==2
    for article in result['articles']:
        for evidence in article['evidence']:
            assert unit.content[evidence['start']:evidence['end']]==evidence['quote']


def test_invented_evidence_is_rejected():
    app=SimpleNamespace(llm=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(content=json.dumps({'articles':[{'title':'X','content':'X','quotes':['invented']}]}))))
    with pytest.raises(ValueError,match='beleg'):
        extract_articles(app,SourceUnit('x','X','actual',()),{'model':'custom/fast','purpose':''})


def test_markdown_emphasis_in_quote_keeps_original_evidence():
    content='1. **React Renderer** zeigt den Status.'
    app=SimpleNamespace(llm=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(content=json.dumps({'articles':[{'title':'Renderer','content':'Zeigt den Status.','quotes':['React Renderer zeigt den Status.']}]}))))
    result=extract_articles(app,SourceUnit('x','X',content,()),{'model':'custom/cheap','purpose':''})
    evidence=result['articles'][0]['evidence'][0]
    assert content[evidence['start']:evidence['end']]==evidence['quote']
    assert evidence['quote']=='**React Renderer** zeigt den Status.'


def test_format_matching_does_not_accept_changed_words():
    app=SimpleNamespace(llm=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(content=json.dumps({'articles':[{'title':'X','content':'X','quotes':['React Renderer speichert den Status.']}]}))))
    with pytest.raises(ValueError,match='beleg'):
        extract_articles(app,SourceUnit('x','X','**React Renderer** zeigt den Status.',()),{'model':'custom/cheap','purpose':''})


@pytest.mark.parametrize('quote', [
    'Der Server nutzt 127.0.0.1 und `MYCELOS_PASSWORD`.',
    'Der Server nutzt `127.0.0.1` und MYCELOS_PASSWORD.',
    'Der Server nutzt 127.0.0.1 und MYCELOS_PASSWORD.',
])
def test_mixed_markdown_formatting_keeps_exact_original_evidence(quote):
    from mycelos.knowledge.source_analysis import _parse_section
    text = 'Einleitung. **Der Server** nutzt `127.0.0.1` und `MYCELOS_PASSWORD`. Ende.'
    raw = json.dumps({'articles':[{'title':'Server','content':'Servereinstellung','quotes':[quote]}]})
    result = _parse_section(raw, text, 100)
    evidence = result[0]['evidence'][0]
    assert evidence == {'start':112, 'end':168, 'quote':'**Der Server** nutzt `127.0.0.1` und `MYCELOS_PASSWORD`.'}
    assert text[evidence['start']-100:evidence['end']-100] == evidence['quote']


@pytest.mark.parametrize('quote', [
    'Der Server nutzt 0.0.0.0 und MYCELOS_PASSWORD.',
    'Der Server benötigt 127.0.0.1 und MYCELOS_PASSWORD.',
    'Der Server nutzt 127.0.0.1 ... MYCELOS_PASSWORD.',
])
def test_format_tolerance_never_accepts_changed_facts(quote):
    from mycelos.knowledge.source_analysis import locate_quote
    with pytest.raises(ValueError, match='Quellenbeleg'):
        locate_quote('**Der Server** nutzt `127.0.0.1` und `MYCELOS_PASSWORD`.', quote)


@pytest.mark.parametrize(('raw', 'finish_reason', 'expected'), [
    ('', 'stop', 'leere Antwort'),
    ('{"articles":[]}', 'length', 'Ausgabelimit'),
    ('not json', 'stop', 'gültiges JSON'),
])
def test_response_failure_has_safe_specific_message(raw, finish_reason, expected):
    from mycelos.knowledge.source_analysis import SourceAnalysisError
    response = SimpleNamespace(content=raw, finish_reason=finish_reason)
    app = SimpleNamespace(llm=SimpleNamespace(complete=lambda *a, **k: response))
    with pytest.raises(SourceAnalysisError, match=expected):
        extract_articles(app, SourceUnit('x', 'X', 'actual', ()), {'model':'custom/cheap', 'purpose':''})


@pytest.mark.parametrize('fence', ['```json', '```'])
def test_single_json_code_block_is_accepted_with_evidence_validation(fence):
    raw = fence + '\n' + json.dumps({'articles':[{'title':'X','content':'Finding','quotes':['actual']} ]}) + '\n```'
    app = SimpleNamespace(llm=SimpleNamespace(complete=lambda *a, **k: SimpleNamespace(content=raw)))
    result = extract_articles(app, SourceUnit('x','X','actual',()), {'model':'custom/cheap','purpose':''})
    assert result['articles'][0]['evidence'][0]['quote'] == 'actual'


@pytest.mark.parametrize('raw', [
    'Here is the result:\n```json\n{"articles":[]}\n```',
    '```json\n{"articles":[]}\n```\nextra',
    '```json\n{"articles":[]}',
])
def test_code_block_does_not_hide_extra_or_incomplete_output(raw):
    app = SimpleNamespace(llm=SimpleNamespace(complete=lambda *a, **k: SimpleNamespace(content=raw)))
    with pytest.raises(ValueError, match='JSON'):
        extract_articles(app, SourceUnit('x','X','actual',()), {'model':'custom/cheap','purpose':''})


def test_invalid_quote_gets_one_correction_with_original_section():
    calls = []
    invalid = {'articles':[{'title':'X','content':'Finding','quotes':['actual ... text']} ]}
    valid = {'articles':[{'title':'X','content':'Finding','quotes':['actual source text']} ]}
    def complete(messages, **kwargs):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(invalid if len(calls)==1 else valid))
    app = SimpleNamespace(llm=SimpleNamespace(complete=complete))
    result = extract_articles(app, SourceUnit('x','X','actual source text',()), {'model':'custom/cheap','purpose':''})
    assert len(calls) == 2
    assert json.loads(calls[1][1]['content'])['text'] == 'actual source text'
    assert calls[1][-2]['role'] == 'assistant'
    assert 'Quellenbeleg' in calls[1][-1]['content']
    assert result['articles'][0]['evidence'][0]['quote'] == 'actual source text'


def test_invalid_quote_correction_is_bounded():
    calls = []
    def complete(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(content=json.dumps({'articles':[{'title':'X','content':'Finding','quotes':['invented']}]}))
    app = SimpleNamespace(llm=SimpleNamespace(complete=complete))
    with pytest.raises(ValueError, match='beleg'):
        extract_articles(app, SourceUnit('x','X','actual',()), {'model':'custom/cheap','purpose':''})
    assert len(calls) == 2


def test_additional_valid_articles_and_quotes_do_not_abort_import():
    quotes = ['Beleg '+str(n) for n in range(9)]
    text = '\n'.join(quotes)
    entries = [{'title':'Thema '+str(n),'content':'Belegte Aussage','quotes':quotes} for n in range(9)]
    calls = []
    def complete(*a, **k):
        calls.append(1)
        return SimpleNamespace(content=json.dumps({'articles':entries}))
    result = extract_articles(SimpleNamespace(llm=SimpleNamespace(complete=complete)),
                              SourceUnit('x','X',text,()), {'model':'custom/cheap','purpose':''})
    assert len(result['articles']) == 9
    assert all(len(a['evidence']) == 9 for a in result['articles'])
    assert len(calls) == 1


def test_excessive_quote_count_has_accurate_error():
    raw = json.dumps({'articles':[{'title':'X','content':'X','quotes':['actual']*33}]})
    app = SimpleNamespace(llm=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(content=raw)))
    with pytest.raises(ValueError, match='höchstens 32'):
        extract_articles(app,SourceUnit('x','X','actual',()),{'model':'custom/cheap','purpose':''})

from mycelos.prompts import build_prompt_variables, PromptLoader


def test_knowledge_prompt_has_no_user_level_guidance(tmp_path):
    from mycelos.app import App
    app = App(tmp_path)
    app.storage.initialize()
    variables = build_prompt_variables(app)
    text = PromptLoader().load("mycelos", **variables)
    assert not variables.get("level_guidance")
    assert "{level_guidance}" not in text

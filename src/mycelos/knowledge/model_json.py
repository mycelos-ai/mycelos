"""Read one JSON value, optionally enclosed in one Markdown code block."""
import json
import re


def parse_model_json(text: str):
    # Strip only an outer fence. Never extract a plausible object from prose,
    # repair incomplete JSON, or remove fences embedded in JSON string values.
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)

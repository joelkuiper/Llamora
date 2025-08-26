import os
from jinja2 import Environment, FileSystemLoader

from config import PROMPT_FILE

prompt_path = os.path.abspath(PROMPT_FILE)
env = Environment(
    loader=FileSystemLoader(os.path.dirname(prompt_path)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

_template = env.get_template(os.path.basename(prompt_path))


def build_prompt(history: list[dict], **context) -> str:
    return _template.render(history=history, **context)


# Opening prompt

opening_prompt_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "prompts", "opening_chatml.j2")
)
_open_env = Environment(
    loader=FileSystemLoader(os.path.dirname(opening_prompt_path)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
_opening_template = _open_env.get_template(os.path.basename(opening_prompt_path))


def build_opening_prompt(yesterday_messages: list[dict], **context) -> str:
    prompt = _opening_template.render(yesterday_messages=yesterday_messages, **context)
    print(prompt)
    return prompt

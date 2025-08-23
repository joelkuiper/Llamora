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


def build_prompt(history: list[dict]) -> str:
    return _template.render(history=history)

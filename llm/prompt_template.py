import os
from jinja2 import Environment, FileSystemLoader

from config import PROMPT_FILE
from app.services.time import humanize

prompt_path = os.path.abspath(PROMPT_FILE)
env = Environment(
    loader=FileSystemLoader(os.path.dirname(prompt_path)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters["humanize"] = humanize

_template = env.get_template(os.path.basename(prompt_path))


def build_prompt(history: list[dict], **context) -> str:
    return _template.render(history=history, is_opening=False, **context)


def build_opening_prompt(yesterday_messages: list[dict], **context) -> str:
    return _template.render(
        yesterday_messages=yesterday_messages, history=[], is_opening=True, **context
    )

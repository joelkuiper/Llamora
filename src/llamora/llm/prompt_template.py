from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from itertools import groupby

from llamora.app.services.time import humanize
from llamora.settings import settings
from llamora.util import resolve_data_path


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

prompt_path = resolve_data_path(
    settings.PROMPTS.prompt_file, fallback_dir=PROMPTS_DIR
)
env = Environment(
    loader=FileSystemLoader(prompt_path.parent),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters["humanize"] = humanize

_template = env.get_template(prompt_path.name)


def build_prompt(history: list[dict], **context) -> str:
    return _template.render(history=history, is_opening=False, **context)


def build_opening_prompt(yesterday_messages: list[dict], **context) -> str:
    grouped_messages = []
    for humanized, group in groupby(
        yesterday_messages,
        key=lambda message: humanize(message["created_at"]),
    ):
        grouped_messages.append(
            {
                "humanized": humanized,
                "messages": list(group),
            }
        )

    return _template.render(
        yesterday_message_groups=grouped_messages,
        history=[],
        is_opening=True,
        **context
    )

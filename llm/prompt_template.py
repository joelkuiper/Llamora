import os
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "prompts")
env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

_template = env.get_template("llamora_phi.j2")


def build_prompt(history: list[dict]) -> str:
    prompt = _template.render(history=history)
    return prompt

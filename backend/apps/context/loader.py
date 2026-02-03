"""Jinja2 模板加载器"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape([]),
    keep_trailing_newline=True,
)


def render(template_name: str, **kwargs: object) -> str:
    """渲染 Jinja2 模板"""
    return _env.get_template(template_name).render(**kwargs)

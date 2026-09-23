{% for entry in context_entries %}
- 第 {{ entry.episode }} 集《{% if entry.title %}{{ entry.title }}{% else %}（无标题）{% endif %}》 钩子：{{ entry.hook }}
{% endfor %}
{% for entry in context_entries %}
- Episode {{ entry.episode }} “{% if entry.title %}{{ entry.title }}{% else %}(untitled){% endif %}” Hook: {{ entry.hook }}
{% endfor %}

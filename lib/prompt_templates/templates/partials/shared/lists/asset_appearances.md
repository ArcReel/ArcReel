{% for entry in entries %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
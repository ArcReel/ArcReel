---
protected: true
---
<characters>
{% for entry in assets.characters %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
</characters>

<scenes>
{% for entry in assets.scenes %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
</scenes>

<props>
{% for entry in assets.props %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
</props>
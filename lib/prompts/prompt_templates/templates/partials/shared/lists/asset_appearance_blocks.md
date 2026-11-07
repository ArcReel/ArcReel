---
protected: true
---
<characters>
{% for entry in assets.characters %}
{% if entry.appearance %}
- {{ entry.name }}: {{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
(none yet)
{% endfor %}
</characters>

<scenes>
{% for entry in assets.scenes %}
{% if entry.appearance %}
- {{ entry.name }}: {{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
(none yet)
{% endfor %}
</scenes>

<props>
{% for entry in assets.props %}
{% if entry.appearance %}
- {{ entry.name }}: {{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
(none yet)
{% endfor %}
</props>
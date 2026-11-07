---
protected: true
---
<characters>
{% for entry in assets.characters %}
- {{ entry.name }}{% if entry.aliases %}(aliases: {{ entry.aliases | join("、") }}){% endif %}{% if entry.appearance %}: {{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
(none yet)
{% endfor %}
</characters>

<scenes>
{% for entry in assets.scenes %}
- {{ entry.name }}{% if entry.aliases %}(aliases: {{ entry.aliases | join("、") }}){% endif %}{% if entry.appearance %}: {{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
(none yet)
{% endfor %}
</scenes>

<props>
{% for entry in assets.props %}
- {{ entry.name }}{% if entry.aliases %}(aliases: {{ entry.aliases | join("、") }}){% endif %}{% if entry.appearance %}: {{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
(none yet)
{% endfor %}
</props>
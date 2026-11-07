---
protected: true
---
<characters>
{% for name in character_names %}
- {{ name }}
{% else %}
(none yet)
{% endfor %}
</characters>

<scenes>
{% for name in scene_names %}
- {{ name }}
{% else %}
(none yet)
{% endfor %}
</scenes>

<props>
{% for name in prop_names %}
- {{ name }}
{% else %}
(none yet)
{% endfor %}
</props>
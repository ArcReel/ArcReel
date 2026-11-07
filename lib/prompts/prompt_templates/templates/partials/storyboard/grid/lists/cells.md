{% for cell in transitions %}
Panel {{ cell.index }} (row{{ cell.row }} col{{ cell.col }}) - {{ cell.from_id }}→{{ cell.to_id }} transition:
  {{ cell.action }}, transitioning to {{ cell.description }}
{% endfor %}
{% for cell in placeholders %}
Panel {{ cell.index }} (row{{ cell.row }} col{{ cell.col }}) - empty placeholder: plain gray background, no content
{% endfor %}
